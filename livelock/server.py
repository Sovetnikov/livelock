import asyncio
import logging
import os
import pickle
import signal
import time
import uuid
from asyncio import StreamReader, IncompleteReadError
from collections import defaultdict
from fnmatch import fnmatch

from livelock.shared import DEFAULT_RELEASE_ALL_TIMEOUT, DEFAULT_BIND_TO, DEFAULT_LIVELOCK_SERVER_PORT, get_settings, DEFAULT_MAX_PAYLOAD, pack_resp, DEFAULT_SHUTDOWN_SUPPORT

ABSOLUTE_PATH = lambda x: os.path.abspath(os.path.join(os.path.abspath(os.path.dirname(__file__)), x))
logger = logging.getLogger(__name__)


class LockStorage(object):
    def __init__(self, release_all_timeout=DEFAULT_RELEASE_ALL_TIMEOUT):
        self.release_all_timeout = release_all_timeout

    def acquire(self, client_id, lock_id, reentrant):
        raise NotImplemented

    def release(self, client_id, lock_id):
        raise NotImplemented

    def release_all(self, client_id):
        raise NotImplemented

    def unrelease_all(self, client_id):
        raise NotImplemented

    def locked(self, lock_id):
        raise NotImplemented

    def set_client_last_address(self, client_id, address):
        raise NotImplemented

    def get_client_last_address(self, client_id):
        raise NotImplemented

    def find(self, pattern):
        raise NotImplemented

    def add_signal(self, lock_id, signal):
        raise NotImplemented

    def has_signal(self, lock_id, signal):
        raise NotImplemented

    def remove_signal(self, lock_id, signal):
        raise NotImplemented

    def dump(self):
        raise NotImplemented

    def disable_write(self):
        raise NotImplemented

    def load_dump(self):
        raise NotImplemented

    def clear_dump(self):
        raise NotImplemented


CONN_REQUIRED_ERROR = 1
WRONG_ARGS = 2
CONN_HAS_ID_ERROR = 3
UNKNOWN_COMMAND_ERROR = 4
PASS_ERROR = 5
RESP_ERROR = 6
SERVER_ERROR = 7
KEY_NOT_EXISTS = 8
SERVER_TERMINATING = 9

ERRORS = {
    CONN_REQUIRED_ERROR: 'CONN required first',
    WRONG_ARGS: 'Wrong number of arguments',
    CONN_HAS_ID_ERROR: 'Already has client id',
    UNKNOWN_COMMAND_ERROR: 'Unknown command',
    PASS_ERROR: 'Wrong or no password',
    RESP_ERROR: 'RESP protocol error',
    SERVER_ERROR: 'Server error',
    KEY_NOT_EXISTS: 'Key does not exists',
    SERVER_TERMINATING: 'Server terminating',
}


class MemoryLockInfo(object):
    __slots__ = ('id', 'time', 'mark_free_after', 'signals')

    def __init__(self, id, time):
        self.id = id
        self.time = time
        self.mark_free_after = None
        self.signals = None

    @property
    def expired(self):
        if self.mark_free_after:
            return time.time() >= self.mark_free_after
        return False

    def add_signal(self, signal):
        if self.signals is None:
            self.signals = set()
        self.signals.add(signal)

    def remove_signal(self, signal):
        if self.signals is not None:
            self.signals.remove(signal)

    def has_signal(self, signal):
        return self.signals is not None and signal in self.signals


class StorageAdaptor(LockStorage):
    def __init__(self, store):
        self.store = store
        self.kill_active = False
        self._storage_operations_in_process = 0
        self._commands_in_process = 0
        self._data_dumped = False
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

    def on_command_start(self):
        self._commands_in_process += 1

    def on_command_end(self):
        self._commands_in_process -= 1
        if self.kill_active and not self._commands_in_process:
            if self._storage_operations_in_process:
                raise Exception('Last command executed, but storage operations counter has value')
            self.die()

    def _on_kill_requested(self, *args, **kwargs):
        # Must wait for active commands to complete, then dump data, close all connections and exit
        self.kill_active = True
        logger.debug(f'Received terminate signal {args} {kwargs}')
        if not self._storage_operations_in_process:
            # Good time to dump and exit(), but there is chance that some client's will not get their responses
            self.dump_before_die()

        # In case storage operations in progress we will dump data after last operations completes
        # and will exit() when last reply is send
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

    def release_all(self, *args, **kwargs):
        with StorageOperationGuard(self):
            return self.store.release_all(*args, **kwargs)

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

    def disable_write(self):
        return self.store.disable_write()

    def load_dump(self):
        return self.store.load_dump()

    def clear_dump(self):
        return self.store.clear_dump()


class InMemoryLockStorage(LockStorage):
    def __init__(self, *args, **kwargs):
        self.client_to_locks = defaultdict(list)
        self.locks_to_client = dict()
        self.all_locks = dict()
        self.client_last_address = dict()
        self._dump_file_name = 'livelock_memstor_dump.pickle'
        self._write_disabled = False

        super().__init__(*args, **kwargs)

    def _delete_lock(self, lock_id):
        client_id = self.locks_to_client.pop(lock_id)
        lock_info = self.all_locks.pop(lock_id)
        self.client_to_locks[client_id].remove(lock_info)

    def _maintenance(self):
        for lock_id, lock_info in dict(self.all_locks).items():
            if lock_info.expired:
                self._delete_lock(lock_id)

    def acquire(self, client_id, lock_id, reentrant=False):
        # Check lock expired
        lock_info = self.all_locks.get(lock_id)
        if lock_info and lock_info.expired:
            self._delete_lock(lock_id)

        locked_by = self.locks_to_client.get(lock_id)
        if locked_by:
            if reentrant and locked_by == client_id:
                # Maybe update lock time?
                return True
            return False
        self.locks_to_client[lock_id] = client_id

        lock_info = MemoryLockInfo(id=lock_id, time=time.time())
        self.client_to_locks[client_id].append(lock_info)
        self.all_locks[lock_id] = lock_info

        logger.debug(f'Acquire {lock_id} for {client_id}')
        return True

    def release(self, client_id, lock_id):
        for lock in self.client_to_locks[client_id]:
            if lock.id == lock_id:
                break
        else:
            return False
        self._delete_lock(lock_id)
        logger.debug(f'Relased {lock_id} for {client_id}')
        return True

    def release_all(self, client_id, timeout=None):
        mark_free_at = time.time() + self.release_all_timeout if not timeout else timeout
        for lock in self.client_to_locks[client_id]:
            lock.mark_free_after = mark_free_at
        logger.debug(f'Marked to free at {mark_free_at} for {client_id}')

    def unrelease_all(self, client_id):
        for lock in self.client_to_locks[client_id]:
            lock.mark_free_after = None
        logger.debug(f'Restored all locks for {client_id}')

    def locked(self, lock_id):
        lock_info = self.all_locks.get(lock_id)
        if lock_info:
            if lock_info.expired:
                self._delete_lock(lock_id)
                return False
            else:
                return True
        return False

    def set_client_last_address(self, client_id, address):
        self.client_last_address[client_id] = address

    def get_client_last_address(self, client_id):
        return self.client_last_address.get(client_id, None)

    def find(self, pattern):
        for lock_id, lock_info in self.all_locks.items():
            if lock_info.expired:
                continue
            if fnmatch(lock_id, pattern):
                yield (lock_id, lock_info.time)

    def add_signal(self, lock_id, signal):
        lock_info = self.all_locks.get(lock_id)
        if not lock_info:
            return KEY_NOT_EXISTS
        lock_info.add_signal(signal)
        return True

    def has_signal(self, lock_id, signal):
        lock_info = self.all_locks.get(lock_id)
        if not lock_info:
            return KEY_NOT_EXISTS
        return lock_info.has_signal(signal)

    def remove_signal(self, lock_id, signal):
        lock_info = self.all_locks.get(lock_id)
        if not lock_info:
            return KEY_NOT_EXISTS
        if lock_info.signals is None:
            return False
        try:
            lock_info.signals.remove(signal)
            return True
        except:
            return False

    def dump(self):
        # Dump data as fast as we can
        # All saved locks will be marked to free by timeout on dump load
        data = dict(dump_time=time.time(),
                    client_to_locks=self.client_to_locks,
                    locks_to_client=self.locks_to_client,
                    all_locks=self.all_locks,
                    client_last_address=self.client_last_address)
        logger.debug('Dumping in memory lock data to %s' % os.path.abspath(self._dump_file_name))
        with open(self._dump_file_name, mode='wb') as f:
            pickle.dump(data, f)
            f.flush()

    def disable_write(self):
        self._write_disabled = True

    def load_dump(self):
        if os.path.isfile(self._dump_file_name):
            logger.debug('Loading in memory lock data from %s' % os.path.abspath(self._dump_file_name))
            with open(self._dump_file_name, mode='rb') as f:
                data = pickle.load(f)
            if data is None:
                raise Exception('No data in dump')
            self.client_to_locks = data['client_to_locks']
            self.locks_to_client = data['locks_to_client']
            self.all_locks = data['all_locks']
            self.client_last_address = data['client_last_address']

            # Delete expired locks
            self._maintenance()

            for client_id in self.client_to_locks.keys():
                self.release_all(client_id, self.release_all_timeout + 1)
            if self.client_to_locks:
                logger.debug(f'Marked to free all locks after {self.release_all_timeout + 1} seconds')

    def clear_dump(self):
        if os.path.isfile(self._dump_file_name):
            logger.debug('Removing in memory lock data file %s' % ABSOLUTE_PATH(self._dump_file_name))
            os.remove(self._dump_file_name)

    def stats(self):
        return dict(lock_count=len(self.all_locks))


class CommandProtocol(asyncio.Protocol):
    def __init__(self, adaptor, max_payload, *args, **kwargs):
        self.transport = None
        self.max_payload = max_payload
        self._reader = None

        super().__init__()

    def data_received(self, data):
        if self.kill_active:
            return
        self._reader.feed_data(data)

    def connection_made(self, transport):
        if self.kill_active:
            return
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
            except (ConnectionAbortedError, ConnectionResetError, IncompleteReadError) as e:
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
        self.client_id = None
        self.client_info = None
        self.shutdown_support = shutdown_support
        self._authorized = None
        self.transport = None
        self.debug = get_settings(None, 'LIVELOCK_DEBUG', False)

        super().__init__(adaptor=adaptor, max_payload=max_payload, *args, **kwargs)

    @property
    def client_display(self):
        if self.transport:
            peername = self.transport.get_extra_info('peername')
        else:
            peername = 'no_peer_address'
        return f'{peername} ({self.client_info}), client={self.client_id}'

    def connection_made(self, transport):
        if self.adaptor.kill_active:
            return
        peername = transport.get_extra_info('peername')
        logger.debug(f'Connection from {peername}')
        self.transport = transport
        super().connection_made(transport)

    def receive_commands_end(self, exc):
        logger.debug(f'Receive command loop ended for {self.client_display}, Exception={exc}')

    def eof_received(self):
        logger.debug(f'EOF received for {self.client_display}')
        return super().eof_received()

    def connection_lost(self, exc):
        peername = self.transport.get_extra_info('peername')
        if self.adaptor.kill_active:
            logger.debug(f'Connection lost on active kill {self.client_display} client={self.client_id}, Exception={exc}')
            return
        logger.debug(f'Connection lost {self.client_display}, Exception={exc}')
        if self.client_id:
            last_address = self.adaptor.get_client_last_address(self.client_id)
            if last_address and last_address == peername:
                # Releasing all client locks only if last known connection is dropped
                # other old connection can be dead
                self.adaptor.release_all(self.client_id)
        super().connection_lost(exc)

    @property
    def kill_active(self):
        return self.adaptor.kill_active

    async def on_command_received(self, command, *args):
        if self.kill_active:
            self.reply_terminating()
            return

        peername = self.transport.get_extra_info('peername')

        verb = command.decode().lower()
        logger.debug(f'Got command {command} from {self.client_display}' if verb != 'pass' else f'Got command PASS from {self.client_display}')

        self.adaptor.on_command_start()
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
                    logger.debug(f'Got client info for {self.client_display} = {client_info}')
                    if self.client_info:
                        if self.client_info != client_info:
                            logger.warning(f'Client info changed for {self.client_display}')
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
                elif verb == 'shutdown' and self.shutdown_support:
                    logger.debug(f'SHUTDOWN invoked by {self.client_display}')
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


async def live_lock_server(bind_to, port, release_all_timeout, password=None, max_payload=None, data_dir=None, shutdown_support=None):
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
    logger.debug(f'Loading dump')
    storage.load_dump()
    stats = storage.stats()

    logger.info(f'Locks loaded from dump: {stats.get("lock_count", 0)}')
    logger.info(f'Starting live lock server at {bind_to}, {port}')
    logger.debug(f'release_all_timeout={release_all_timeout}')
    logger.debug(f'data_dir={data_dir}')

    adaptor = StorageAdaptor(storage)

    server = await loop.create_server(lambda: LiveLockProtocol(adaptor=adaptor, password=password, max_payload=max_payload, shutdown_support=shutdown_support), bind_to, port)
    def exception_handler(loop, context):
        if not isinstance(context['exception'], SystemExit):
            raise context['exception']

    loop.set_exception_handler(exception_handler)
    async with server:
        await server.serve_forever()


def start(bind_to=DEFAULT_BIND_TO, port=None, release_all_timeout=None, password=None, max_payload=None, data_dir=None, shutdown_support=None):
    logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')
    asyncio.run(live_lock_server(bind_to=get_settings(bind_to, DEFAULT_BIND_TO, 'LIVELOCK_BIND_TO'),
                                 port=get_settings(port, 'LIVELOCK_PORT', DEFAULT_LIVELOCK_SERVER_PORT),
                                 release_all_timeout=get_settings(release_all_timeout, 'LIVELOCK_RELEASE_ALL_TIMEOUT', DEFAULT_RELEASE_ALL_TIMEOUT),
                                 password=get_settings(password, 'LIVELOCK_PASSWORD', None),
                                 max_payload=get_settings(max_payload, 'LIVELOCK_MAX_PAYLOAD', DEFAULT_MAX_PAYLOAD),
                                 data_dir=get_settings(data_dir, 'LIVELOCK_DATA_DIR', None),
                                 shutdown_support=get_settings(shutdown_support, 'LIVELOCK_SHUTDOWN_SUPPORT', DEFAULT_SHUTDOWN_SUPPORT),
                                 ))
