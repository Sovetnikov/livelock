import asyncio
import logging
import os
import signal
import socket
import time
import uuid
from asyncio import StreamReader, IncompleteReadError, Event
from timeit import default_timer

from livelock.memory_storage import InMemoryLockStorage
from livelock.shared import DEFAULT_RELEASE_ALL_TIMEOUT, DEFAULT_BIND_TO, DEFAULT_LIVELOCK_SERVER_PORT, get_settings, DEFAULT_MAX_PAYLOAD, pack_resp, DEFAULT_SHUTDOWN_SUPPORT, \
    DEFAULT_PROMETHEUS_PORT, DEFAULT_TCP_KEEPALIVE_TIME, DEFAULT_TCP_KEEPALIVE_INTERVAL, DEFAULT_TCP_KEEPALIVE_PROBES, DEFAULT_LOGLEVEL, DEFAULT_DISABLE_DUMP_LOAD, \
    DEFAULT_TCP_USER_TIMEOUT_SECONDS, DEFAULT_MAINTENANCE_TIMEOUT_MS, DEFAULT_MAINTENANCE_PERIOD, get_float_settings, get_int_settings, SERVER_TERMINATING, CONN_HAS_ID_ERROR, \
    WRONG_ARGS, CONN_REQUIRED_ERROR, UNKNOWN_COMMAND_ERROR, PASS_ERROR, ERRORS, LazyArg
from livelock.stats import latency, max_lock_live_time, stats_collection_time, prometheus_client_installed, maintenance_time, stats_collection_count, maintenance_count, lock_count
from livelock.storage import LockStorage
from livelock.tcp_opts import set_tcp_keepalive

ABSOLUTE_PATH = lambda x: os.path.abspath(os.path.join(os.path.abspath(os.path.dirname(__file__)), x))
logger = logging.getLogger(__name__)


# Надо сделать
# 3. Протоколирование длительности операций
# 4. Хранение clientinfo


class StorageAdaptor(LockStorage):
    def __init__(self, store):
        self.store = store
        self.kill_active = False
        self._storage_operations_in_process = 0
        self._commands_in_process = 0
        self._data_dumped = False
        self._maintenance_event = Event()

        signal.signal(signal.SIGINT, self._on_kill_requested)
        signal.signal(signal.SIGTERM, self._on_kill_requested)

        super().__init__()

    def terminate(self):
        self._on_kill_requested()

    def on_storage_operation_start(self):
        self._storage_operations_in_process += 1

    def on_storage_operation_end(self):
        self._storage_operations_in_process -= 1
        if self._storage_operations_in_process < 0:
            raise Exception('Storage active operations counter broken')
        if self.kill_active and not self._storage_operations_in_process:
            # Last storage operation ended, good point to dump storage data
            # then exit() after sending replies to clients
            self.dump_before_die()

    def is_commands_in_process(self):
        return self._commands_in_process > 0

    async def on_command_start(self):
        await self._maintenance_event.wait()
        self._commands_in_process += 1

    def on_command_end(self):
        self._commands_in_process -= 1
        if self.kill_active and not self._commands_in_process:
            if self._storage_operations_in_process:
                logger.error('Last command executed, but storage operations counter has value')
            self.die()

    def _on_kill_requested(self, *args, **kwargs):
        # Must wait for active commands to complete, then dump data, close all connections and exit
        self.kill_active = True
        logger.info('Received terminate signal %s %s', args, kwargs)
        if not self._storage_operations_in_process:
            # Good time to dump and exit(), but there is chance that some client's will not get their responses
            self.dump_before_die()

        # In case any storage operations is in progress, we will dump data after last operations completes
        # and will exit() when last reply is sent
        if not self._commands_in_process:
            self.die()

    def dump_before_die(self):
        # Just dump data, because all storage operations ended
        self.store.dump()
        self._data_dumped = True
        logger.debug('Dumped data')

    def die(self):
        # All commands processed, can exit safely
        if not self._data_dumped:
            self.store.dump()
        logger.debug('Exit')
        exit(1)

    def acquire(self, *args, **kwargs):
        with StorageOperationGuard(self):
            return self.store.acquire(*args, **kwargs)

    def release(self, *args, **kwargs):
        with StorageOperationGuard(self):
            return self.store.release(*args, **kwargs)

    def release_all(self, client_id):
        with StorageOperationGuard(self):
            return self.store.release_all(client_id=client_id, timeout=self.release_all_timeout)

    def unrelease_all(self, *args, **kwargs):
        with StorageOperationGuard(self):
            return self.store.unrelease_all(*args, **kwargs)

    def locked(self, *args, **kwargs):
        return self.store.locked(*args, **kwargs)

    def set_client_last_address(self, *args, **kwargs):
        with StorageOperationGuard(self):
            return self.store.set_client_last_address(*args, **kwargs)

    def add_signal(self, *args, **kwargs):
        with StorageOperationGuard(self):
            return self.store.add_signal(*args, **kwargs)

    def has_signal(self, *args, **kwargs):
        return self.store.has_signal(*args, **kwargs)

    def remove_signal(self, *args, **kwargs):
        with StorageOperationGuard(self):
            return self.store.remove_signal(*args, **kwargs)

    def get_client_last_address(self, *args, **kwargs):
        return self.store.get_client_last_address(*args, **kwargs)

    def find(self, *args, **kwargs):
        return self.store.find(*args, **kwargs)

    def dump(self):
        return self.store.dump()

    def load_dump(self):
        return self.store.load_dump()

    def clear_dump(self):
        return self.store.clear_dump()

    def stats(self):
        return self.store.stats()

    def maintenance(self, *args, **kwargs):
        logger.debug('Doing maintenance')
        try:
            self._maintenance_event.clear()
            return self.store.maintenance(*args, **kwargs)
        finally:
            self._maintenance_event.set()


class CommandProtocol(asyncio.Protocol):
    def __init__(self, tcp_keepalive_time=None, tcp_keepalive_interval=None, tcp_keepalive_probes=None, tcp_user_timeout_seconds=None, *args, **kwargs):
        self.transport = None
        self._reader = None

        """
        https://blog.cloudflare.com/when-tcp-sockets-refuse-to-die/
        
        http://coryklein.com/tcp/2015/11/25/custom-configuration-of-tcp-socket-keep-alive-timeouts.html
        Keep-Alive Process
        There are three configurable properties that determine how Keep-Alives work. On Linux they are1:
        
        tcp_keepalive_time
        default 7200 seconds
        tcp_keepalive_probes
        default 9
        tcp_keepalive_intvl
        default 75 seconds
        The process works like this:
        
        1. Client opens TCP connection
        2. If the connection is silent for tcp_keepalive_time seconds, send a single empty ACK packet.1
        3. Did the server respond with a corresponding ACK of its own?
            - No
            1. Wait tcp_keepalive_intvl seconds, then send another ACK
            2. Repeat until the number of ACK probes that have been sent equals tcp_keepalive_probes.
            3. If no response has been received at this point, send a RST and terminate the connection.
            - Yes: Return to step 2
        """
        self.tcp_keepalive_time = int(tcp_keepalive_time)
        self.tcp_keepalive_interval = int(tcp_keepalive_interval)
        self.tcp_keepalive_probes = int(tcp_keepalive_probes)
        self.tcp_user_timeout_seconds = int(tcp_user_timeout_seconds)
        super().__init__(*args, **kwargs)

    def data_received(self, data):
        if self.kill_active:
            return
        self._reader.feed_data(data)

    def connection_made(self, transport):
        if self.kill_active:
            return

        sock = transport.get_extra_info('socket')
        set_tcp_keepalive(sock, opts=dict(tcp_keepalive=True,
                                          tcp_keepalive_idle=self.tcp_keepalive_time,
                                          tcp_keepalive_intvl=self.tcp_keepalive_interval,
                                          tcp_keepalive_cnt=self.tcp_keepalive_probes,
                                          ))
        # https://eklitzke.org/the-caveats-of-tcp-nodelay
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if hasattr(socket, 'TCP_USER_TIMEOUT'):
            logger.debug('Setting TCP_USER_TIMEOUT to %s', self.tcp_user_timeout_seconds * 1000)
            sock.setsockopt(socket.SOL_TCP, socket.TCP_USER_TIMEOUT, self.tcp_user_timeout_seconds * 1000)
        self.transport = transport
        self._reader = StreamReader()
        self._reader.set_transport(transport)

        loop = asyncio.get_event_loop()
        loop.create_task(self.receive_commands())
        super().connection_made(transport)

    def connection_lost(self, exc):
        if self.kill_active:
            return
        if self._reader is not None:
            if exc is None:
                self._reader.feed_eof()
            else:
                self._reader.set_exception(exc)
        super().connection_lost(exc)

    def eof_received(self):
        if self.kill_active:
            return
        self._reader.feed_eof()
        return super().eof_received()

    async def _read_int(self):
        if self.kill_active:
            return
        line = await self._reader.readuntil(b'\r\n')
        return int(line.decode().strip())

    async def _read_float(self):
        if self.kill_active:
            return
        line = await self._reader.readuntil(b'\r\n')
        return float(line.decode().strip())

    async def _read_bytes(self):
        if self.kill_active:
            return
        len = await self._read_int()
        line = await self._reader.readexactly(max(2, len + 2))
        if line[-1] != ord(b'\n'):
            raise Exception(r"line[-1] != ord(b'\n')")
        if len < 0:
            return None
        if len == 0:
            return b''
        return line[:-2]

    async def _read_array(self):
        if self.kill_active:
            return
        len = await self._read_int()
        r = []
        while len:
            c = await self._reader.readexactly(1)
            value = await self._receive_resp(c)
            r.append(value)
            len -= 1
        return r

    async def _receive_resp(self, c):
        if self.kill_active:
            return
        if c == b':':
            return await self._read_int()
        elif c == b'$':
            return await self._read_bytes()
        elif c == b'*':
            return await self._read_array()
        elif c == b',':
            return await self._read_float()
        else:
            raise Exception('Unknown RESP start char %s' % c)

    async def receive_commands(self):
        while True:
            if self.kill_active:
                self.reply_terminating()
                return
            try:
                c = await self._reader.readexactly(1)
                if c in b':*$,':
                    value = await self._receive_resp(c)
                    if not isinstance(value, list):
                        value = [value, ]
                else:
                    command = c + await self._reader.readuntil(b'\r\n')
                    value = [x.strip().encode() for x in command.decode().split(' ')]
            except (ConnectionAbortedError, ConnectionResetError, IncompleteReadError, TimeoutError) as e:
                # Connection is closed
                self.receive_commands_end(e)
                return
            await self.on_command_received(*value)

    async def on_command_received(self, command):
        raise NotImplemented()

    def reply_terminating(self):
        raise NotImplemented()

    def receive_commands_end(self, exc):
        raise NotImplemented()


class LiveLockProtocol(CommandProtocol):
    def __init__(self, adaptor, password, max_payload, shutdown_support=False, *args, **kwargs):
        self.password = password
        self.adaptor = adaptor
        self.max_payload = max_payload

        self.client_id = None
        self.client_info = None
        self.shutdown_support = shutdown_support
        self._authorized = None
        self.transport = None

        self.debug = get_settings(None, 'LIVELOCK_DEBUG', False)
        self.lazy_client_display = LazyArg(self.client_display)

        super().__init__(*args, **kwargs)

    def client_display(self):
        if self.transport:
            peername = self.transport.get_extra_info('peername')
        else:
            peername = 'no_peer_address'
        return f'{peername} ({self.client_info}), client={self.client_id}'

    def connection_made(self, transport):
        if self.adaptor.kill_active:
            return
        logger.debug('Connection from %s', LazyArg(lambda: transport.get_extra_info('peername')))
        self.transport = transport
        super().connection_made(transport)

    def receive_commands_end(self, exc):
        logger.debug('Receive command loop ended for %s, Exception=%s', self.lazy_client_display, exc)

    def eof_received(self):
        logger.debug('EOF received for %s', self.lazy_client_display)
        return super().eof_received()

    def connection_lost(self, exc):
        peername = self.transport.get_extra_info('peername')
        if self.adaptor.kill_active:
            logger.debug('Connection lost on active kill %s client=%s, Exception=%s', self.lazy_client_display, self.client_id, exc)
            return
        logger.debug('Connection lost %s, Exception=%s', self.lazy_client_display, exc)
        if self.client_id:
            last_address = self.adaptor.get_client_last_address(self.client_id)
            if last_address is None:
                logger.warning('Client last address is empty')
            if (last_address and last_address == peername) or last_address is None:
                # Releasing all client locks only if last known connection is dropped
                # other old connection can be dead
                self.adaptor.release_all(self.client_id)
        super().connection_lost(exc)

    @property
    def kill_active(self):
        return self.adaptor.kill_active

    async def on_command_received(self, command, *args):
        st = default_timer()
        verb = 'unknown'
        try:
            verb = await self.process_command(command, *args)
        finally:
            latency.labels(verb if verb is not None else 'unknown').observe(max(default_timer() - st, 0))

    async def process_command(self, command, *args):
        if self.kill_active:
            self.reply_terminating()
            return

        peername = self.transport.get_extra_info('peername')

        verb = command.decode().lower()
        logger.debug('Got command %s from %s', command, self.lazy_client_display)

        await self.adaptor.on_command_start()
        try:
            if self.password and not self._authorized:
                if verb == 'pass':
                    if len(args) == 1 and args[0]:
                        password = args[0].decode()
                        if password == self.password:
                            self._authorized = True
                            self._reply(True)
                            return
                self._reply(PASS_ERROR)
                self.transport.close()

            if verb == 'conn':
                if self.client_id:
                    self._reply(CONN_HAS_ID_ERROR)
                if args:
                    if not args[0]:
                        self._reply(WRONG_ARGS)
                        return
                    self.client_id = args[0].decode()
                    # Restoring client locks
                    self.adaptor.unrelease_all(self.client_id)
                else:
                    self.client_id = str(uuid.uuid4())
                # Saving client last connection source address for making decision to call release_all on connection lost
                self.adaptor.set_client_last_address(self.client_id, peername)
                self._reply(self.client_id)
                return
            else:
                if not self.client_id:
                    self._reply(CONN_REQUIRED_ERROR)
                    return
                if verb == 'conninfo':
                    if len(args) != 1 or not args[0]:
                        self._reply(WRONG_ARGS)
                        return
                    try:
                        client_info = args[0].decode()
                    except:
                        self._reply(WRONG_ARGS)
                        return
                    logger.debug('Got client info for %s = %s', self.lazy_client_display, client_info)
                    if self.client_info:
                        if self.client_info != client_info:
                            logger.warning('Client info changed for %s', self.lazy_client_display)
                    self.client_info = client_info
                    self._reply(True)
                elif verb in ('aq', 'aqr'):
                    if len(args) != 1 or not args[0]:
                        self._reply(WRONG_ARGS)
                        return
                    try:
                        lock_id = args[0].decode()
                    except:
                        self._reply(WRONG_ARGS)
                        return
                    res = self.acquire(client_id=self.client_id, lock_id=lock_id, reentrant=(verb == 'aqr'))
                    self._reply(res)
                elif verb == 'release':
                    if len(args) != 1 or not args[0]:
                        self._reply(WRONG_ARGS)
                        return
                    try:
                        lock_id = args[0].decode()
                    except:
                        self._reply(WRONG_ARGS)
                        return
                    res = self.release(client_id=self.client_id, lock_id=lock_id)
                    self._reply(res)
                elif verb == 'locked':
                    if len(args) != 1 or not args[0]:
                        self._reply(WRONG_ARGS)
                        return
                    try:
                        lock_id = args[0].decode()
                    except:
                        self._reply(WRONG_ARGS)
                        return
                    res = self.locked(lock_id=lock_id)
                    self._reply(res)
                elif verb == 'sigset':
                    if len(args) != 2 or not args[0] or not args[1]:
                        self._reply(WRONG_ARGS)
                        return
                    try:
                        lock_id = args[0].decode()
                        signal = args[1].decode()
                    except:
                        self._reply(WRONG_ARGS)
                        return
                    res = self.add_signal(lock_id, signal)
                    self._reply(res)
                elif verb == 'sigexists':
                    if len(args) != 2 or not args[0] or not args[1]:
                        self._reply(WRONG_ARGS)
                        return
                    try:
                        lock_id = args[0].decode()
                        signal = args[1].decode()
                    except:
                        self._reply(WRONG_ARGS)
                        return
                    res = self.has_signal(lock_id, signal)
                    self._reply(res)
                elif verb == 'sigdel':
                    if len(args) != 2 or not args[0] or not args[1]:
                        self._reply(WRONG_ARGS)
                        return
                    try:
                        lock_id = args[0].decode()
                        signal = args[1].decode()
                    except:
                        self._reply(WRONG_ARGS)
                        return
                    res = self.remove_signal(lock_id, signal)
                    self._reply(res)
                elif verb == 'ping':
                    self._reply('PONG')
                elif verb == 'find':
                    if len(args) != 1 or not args[0]:
                        self._reply(WRONG_ARGS)
                        return
                    result = list(self.adaptor.find(args[0].decode()))
                    self._reply_data(result)
                elif verb == 'stats':
                    if len(args):
                        self._reply(WRONG_ARGS)
                        return
                    result = self.adaptor.stats()
                    self._reply_data(result)
                elif verb == 'shutdown' and self.shutdown_support:
                    logger.debug('SHUTDOWN invoked by %s', self.lazy_client_display)
                    self.adaptor.terminate()
                    self._reply('1')
                else:
                    if self.debug:
                        if verb == 'dump':
                            if hasattr(self.adaptor, 'dump'):
                                self.adaptor.dump()
                                self._reply('1')
                            else:
                                self._reply('0')
                            return
                    self._reply(UNKNOWN_COMMAND_ERROR)
        finally:
            self.adaptor.on_command_end()
        # For stats returning verb
        return verb

    def _reply_data(self, data):
        payload = pack_resp(data)
        self.transport.write(payload)

    def reply_terminating(self):
        self._reply(SERVER_TERMINATING)

    def _reply(self, content):
        prefix = '+'
        if content is True:
            content = '1'
        elif content is False:
            content = '0'
        elif isinstance(content, int):
            content = '%s %s' % (content, ERRORS[content])
            prefix = '-'

        payload = '%s%s\r\n' % (prefix, content)
        payload = payload.encode()
        self.transport.write(payload)

    def acquire(self, client_id, lock_id, reentrant):
        res = self.adaptor.acquire(client_id, lock_id, reentrant)
        return res

    def release(self, client_id, lock_id):
        res = self.adaptor.release(client_id, lock_id)
        return res

    def locked(self, lock_id):
        res = self.adaptor.locked(lock_id)
        return res

    def add_signal(self, lock_id, signal):
        res = self.adaptor.add_signal(lock_id, signal.lower())
        return res

    def has_signal(self, lock_id, signal):
        res = self.adaptor.has_signal(lock_id, signal.lower())
        return res

    def remove_signal(self, lock_id, signal):
        res = self.adaptor.remove_signal(lock_id, signal.lower())
        return res


class StorageOperationGuard(object):
    def __init__(self, parent):
        self.parent = parent

    def __enter__(self):
        if self.parent.kill_active:
            raise Exception('Lock storage write operations disabled')
        self.parent.on_storage_operation_start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.parent.on_storage_operation_end()


async def stats_collector(adaptor):
    while True:
        st = time.time()
        max_live_lock = 0
        locks = list(adaptor.find('*'))
        if locks:
            now = time.time()
            first_lock = min(locks, key=lambda x: x[1])
            max_live_lock = int(now - first_lock[1])

        lock_count.set(len(locks))
        max_lock_live_time.set(max_live_lock)
        stats_collection_time.inc(time.time() - st)
        stats_collection_count.inc()
        await asyncio.sleep(5)


async def maintenance_scheduler(adaptor, period, timeout_ms):
    """
    "Sampling" scheduler of maintenance call
    """
    logger.debug('Starting maintenance scheduler, period %s, timeout_ms %s', period, timeout_ms)
    while True:
        if not adaptor.is_commands_in_process() and not adaptor.kill_active:
            # Can do maintenance
            st = time.time()
            adaptor.maintenance(timeout_ms=timeout_ms)
            ed = time.time()
            t = (ed - st) * 1000
            logger.debug('Maintenance time %s ms', t)
            if t > timeout_ms:
                logger.warning('Maintenance timeout exceeded, configured=%s ms, actual=%s ms, ', timeout_ms, t)
            maintenance_time.inc(t)
            maintenance_count.inc()
        else:
            logger.debug('Skipping maintenance')
        await asyncio.sleep(period)


async def live_lock_server(bind_to, port, release_all_timeout, password=None,
                           max_payload=None,
                           data_dir=None,
                           shutdown_support=None,
                           tcp_keepalive_time=None,
                           tcp_keepalive_interval=None,
                           tcp_keepalive_probes=None,
                           tcp_user_timeout_seconds=None,
                           disable_dump_load=None,
                           ):
    # Sanitize values
    tcp_keepalive_time = int(tcp_keepalive_time) if tcp_keepalive_time else None
    tcp_keepalive_interval = int(tcp_keepalive_interval) if tcp_keepalive_interval else None
    tcp_keepalive_probes = int(tcp_keepalive_probes) if tcp_keepalive_probes else None
    tcp_user_timeout_seconds = int(tcp_user_timeout_seconds) if tcp_user_timeout_seconds else None

    loop = asyncio.get_running_loop()

    try:
        port = int(port)
    except:
        raise Exception(f'Live lock server port is not integer: {port}')

    if data_dir:
        os.chdir(data_dir)
    else:
        data_dir = os.getcwd()

    storage = InMemoryLockStorage(release_all_timeout=release_all_timeout)
    if not disable_dump_load:
        logger.info('Loading dump')
        storage.load_dump()
        stats = storage.stats()

        logger.info('Locks loaded from dump: %s', stats.get("lock_count", 0))
    logger.info('Starting live lock server at %s, %s', bind_to, port)
    logger.debug('release_all_timeout=%s', release_all_timeout)
    logger.debug('data_dir=%s', data_dir)

    adaptor = StorageAdaptor(storage)

    server = await loop.create_server(lambda: LiveLockProtocol(adaptor=adaptor,
                                                               password=password,
                                                               max_payload=max_payload,
                                                               shutdown_support=shutdown_support,
                                                               tcp_keepalive_time=tcp_keepalive_time,
                                                               tcp_keepalive_interval=tcp_keepalive_interval,
                                                               tcp_keepalive_probes=tcp_keepalive_probes,
                                                               tcp_user_timeout_seconds=tcp_user_timeout_seconds,
                                                               ), bind_to, port)

    def exception_handler(loop, context):
        if not isinstance(context['exception'], SystemExit):
            raise context['exception']

    loop.set_exception_handler(exception_handler)

    if prometheus_client_installed:
        stats_collector_task = loop.create_task(stats_collector(adaptor))

    maintenance_scheduler_task = loop.create_task(maintenance_scheduler(adaptor,
                                                                        period=get_float_settings(None, 'LIVELOCK_MAINTENANCE_PERIOD', DEFAULT_MAINTENANCE_PERIOD),
                                                                        timeout_ms=get_int_settings(None, 'LIVELOCK_MAINTENANCE_TIMEOUT_MS', DEFAULT_MAINTENANCE_TIMEOUT_MS)))

    async with server:
        await server.serve_forever()

    if prometheus_client_installed:
        stats_collector_task.cancel()
    maintenance_scheduler_task.cancel()


def start(bind_to=DEFAULT_BIND_TO, port=None, release_all_timeout=None, password=None,
          max_payload=None,
          data_dir=None,
          shutdown_support=None,
          tcp_keepalive_time=None,
          tcp_keepalive_interval=None,
          tcp_keepalive_probes=None,
          tcp_user_timeout_seconds=None,
          disable_dump_load=None,
          ):
    sentry_dsn = get_settings(None, 'SENTRY_DSN', None)
    if sentry_dsn:
        logger.debug('Turning ON sentry')
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=sentry_dsn)
        except ImportError:
            logger.error('No sentry-sdk installed')

    env_loglevel = get_settings(None, 'LIVELOCK_LOGLEVEL', DEFAULT_LOGLEVEL)
    loglevel = getattr(logging, env_loglevel.upper())

    logging.basicConfig(level=loglevel, format='%(name)s:[%(levelname)s]: %(message)s')
    prometheus_port = get_settings(port, 'LIVELOCK_PROMETHEUS_PORT', DEFAULT_PROMETHEUS_PORT)
    if prometheus_port:
        try:
            prometheus_port = int(prometheus_port)
        except:
            logger.critical('Wrong prometheus port %s', prometheus_port)
            prometheus_port = None
    if prometheus_port:
        try:
            from prometheus_client import start_http_server
        except ImportError:
            logger.info('Prometheus port is set but prometheus_client not installed (pip install prometheus-client)')
            prometheus_port = None
        if prometheus_port:
            logger.info('Starting prometheus metrics server at port %s', prometheus_port)
            start_http_server(prometheus_port)

    asyncio.run(live_lock_server(bind_to=get_settings(bind_to, DEFAULT_BIND_TO, 'LIVELOCK_BIND_TO'),
                                 port=get_settings(port, 'LIVELOCK_PORT', DEFAULT_LIVELOCK_SERVER_PORT),
                                 release_all_timeout=get_settings(release_all_timeout, 'LIVELOCK_RELEASE_ALL_TIMEOUT', DEFAULT_RELEASE_ALL_TIMEOUT),
                                 password=get_settings(password, 'LIVELOCK_PASSWORD', None),
                                 max_payload=get_settings(max_payload, 'LIVELOCK_MAX_PAYLOAD', DEFAULT_MAX_PAYLOAD),
                                 data_dir=get_settings(data_dir, 'LIVELOCK_DATA_DIR', None),
                                 shutdown_support=get_settings(shutdown_support, 'LIVELOCK_SHUTDOWN_SUPPORT', DEFAULT_SHUTDOWN_SUPPORT),
                                 tcp_keepalive_time=get_settings(tcp_keepalive_time, 'LIVELOCK_TCP_KEEPALIVE_TIME', DEFAULT_TCP_KEEPALIVE_TIME),
                                 tcp_keepalive_interval=get_settings(tcp_keepalive_interval, 'LIVELOCK_TCP_KEEPALIVE_INTERVAL', DEFAULT_TCP_KEEPALIVE_INTERVAL),
                                 tcp_keepalive_probes=get_settings(tcp_keepalive_probes, 'LIVELOCK_TCP_KEEPALIVE_PROBES', DEFAULT_TCP_KEEPALIVE_PROBES),
                                 tcp_user_timeout_seconds=get_settings(tcp_user_timeout_seconds, 'LIVELOCK_TCP_USER_TIMEOUT_SECONDS', DEFAULT_TCP_USER_TIMEOUT_SECONDS),
                                 disable_dump_load=get_settings(disable_dump_load, 'LIVELOCK_DISABLE_DUMP_LOAD', DEFAULT_DISABLE_DUMP_LOAD),
                                 ))
