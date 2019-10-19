import asyncio
import logging
import time
import uuid
from asyncio import StreamReader, IncompleteReadError
from collections import defaultdict
from fnmatch import fnmatch

from livelock.shared import DEFAULT_RELEASE_ALL_TIMEOUT, DEFAULT_BIND_TO, DEFAULT_LIVELOCK_SERVER_PORT, get_settings, DEFAULT_MAX_PAYLOAD, pack_resp

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


CONN_REQUIRED_ERROR = 1
WRONG_ARGS = 2
CONN_HAS_ID_ERROR = 3
UNKNOWN_COMMAND_ERROR = 4
PASS_ERROR = 5
RESP_ERROR = 6
SERVER_ERROR = 7
KEY_NOT_EXISTS = 8

ERRORS = {
    CONN_REQUIRED_ERROR: 'CONN required first',
    WRONG_ARGS: 'Wrong number of arguments',
    CONN_HAS_ID_ERROR: 'Already has client id',
    UNKNOWN_COMMAND_ERROR: 'Unknown command',
    PASS_ERROR: 'Wrong or no password',
    RESP_ERROR: 'RESP protocol error',
    SERVER_ERROR: 'Server error',
    KEY_NOT_EXISTS: 'Key does not exists'
}


class MemoryLockInfo(object):
    __slots__ = ('id', 'time', 'mark_free_after', 'signals')

    def __init__(self, id, time):
        self.id = id
        self.time = time
        self.mark_free_after = None
        self.signals = None

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

class InMemoryLockStorage(LockStorage):
    def __init__(self, *args, **kwargs):
        self.client_to_locks = defaultdict(list)
        self.locks_to_client = dict()
        self.all_locks = dict()
        self.client_last_address = dict()
        super().__init__(*args, **kwargs)

    def _delete_lock(self, lock_id):
        client_id = self.locks_to_client.pop(lock_id)
        lock_info = self.all_locks.pop(lock_id)
        self.client_to_locks[client_id].remove(lock_info)

    def acquire(self, client_id, lock_id, reentrant=False):
        # Check lock expired
        lock_info = self.all_locks.get(lock_id)
        if lock_info and lock_info.expired():
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

    def release_all(self, client_id):
        mark_free_at = time.time() + self.release_all_timeout
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
            if lock_info.expired():
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
            if lock_info.expired():
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


class CommandProtocol(asyncio.Protocol):
    def __init__(self, max_payload, *args, **kwargs):
        self.transport = None
        self.max_payload = max_payload
        self._reader = None
        super().__init__(
            # stream_reader=self._reader
        )

    def data_received(self, data):
        self._reader.feed_data(data)

    def connection_made(self, transport):
        self.transport = transport
        self._reader = StreamReader()
        self._reader.set_transport(transport)

        loop = asyncio.get_event_loop()
        loop.create_task(self.receive_commands())
        super().connection_made(transport)

    def connection_lost(self, exc):
        if self._reader is not None:
            if exc is None:
                self._reader.feed_eof()
            else:
                self._reader.set_exception(exc)
        super().connection_lost(exc)

    def eof_received(self):
        self._reader.feed_eof()
        return super().eof_received()

    async def _read_int(self):
        line = await self._reader.readuntil(b'\r\n')
        return int(line.decode().strip())

    async def _read_float(self):
        line = await self._reader.readuntil(b'\r\n')
        return float(line.decode().strip())

    async def _read_bytes(self):
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
        len = await self._read_int()
        r = []
        while len:
            c = await self._reader.readexactly(1)
            value = await self._receive_resp(c)
            r.append(value)
            len -= 1
        return r

    async def _receive_resp(self, c):
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
            try:
                c = await self._reader.readexactly(1)
                if c in b':*$,':
                    value = await self._receive_resp(c)
                    if not isinstance(value, list):
                        value = [value, ]
                else:
                    command = c + await self._reader.readuntil(b'\r\n')
                    value = [x.strip().encode() for x in command.decode().split(' ')]
            except IncompleteReadError:
                # Connection is closed
                return
            await self.on_command_received(*value)

    async def on_command_received(self, command):
        raise NotImplemented()


class LiveLockProtocol(CommandProtocol):
    def __init__(self, storage, password, max_payload, *args, **kwargs):
        self.password = password
        super().__init__(max_payload=max_payload, *args, **kwargs)
        self.storage = storage
        self.client_id = None
        self._authorized = None

    def connection_made(self, transport):
        peername = transport.get_extra_info('peername')
        logger.debug(f'Connection from {peername}')
        self.transport = transport
        super().connection_made(transport)

    def connection_lost(self, exc):
        peername = self.transport.get_extra_info('peername')
        logger.debug(f'Connection lost {peername} client={self.client_id}, Exception={exc}')
        if self.client_id:
            last_address = self.storage.get_client_last_address(self.client_id)
            if last_address and last_address == peername:
                # Releasing all client locks only if last known connection is dropped
                # other old connection can be dead
                self.storage.release_all(self.client_id)
        super().connection_lost(exc)

    async def on_command_received(self, command, *args):
        peername = self.transport.get_extra_info('peername')

        verb = command.decode().lower()
        logger.debug(f'Got command {command} from {peername}' if verb != 'pass' else f'Got command PASS from {peername}')

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
                self.storage.unrelease_all(self.client_id)
            else:
                self.client_id = str(uuid.uuid4())
            # Saving client last connection source address for making decision to call release_all on connection lost
            self.storage.set_client_last_address(self.client_id, peername)
            self._reply(self.client_id)
            return
        else:
            if not self.client_id:
                self._reply(CONN_REQUIRED_ERROR)
                return
            if verb in ('aq', 'aqr'):
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
                result = list(self.storage.find(args[0].decode()))
                self._reply_data(result)
            else:
                self._reply(UNKNOWN_COMMAND_ERROR)

    def _reply_data(self, data):
        payload = pack_resp(data)
        self.transport.write(payload)

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
        res = self.storage.acquire(client_id, lock_id, reentrant)
        return res

    def release(self, client_id, lock_id):
        res = self.storage.release(client_id, lock_id)
        return res

    def locked(self, lock_id):
        res = self.storage.locked(lock_id)
        return res

    def add_signal(self, lock_id, signal):
        res = self.storage.add_signal(lock_id, signal.lower())
        return res

    def has_signal(self, lock_id, signal):
        res = self.storage.has_signal(lock_id, signal.lower())
        return res

    def remove_signal(self, lock_id, signal):
        res = self.storage.remove_signal(lock_id, signal.lower())
        return res


async def live_lock_server(bind_to, port, release_all_timeout, password=None, max_payload=None):
    loop = asyncio.get_running_loop()

    try:
        port = int(port)
    except:
        raise Exception(f'Live lock server port is not integer: {port}')

    storage = InMemoryLockStorage(release_all_timeout=release_all_timeout)
    logger.debug(f'Starting live lock server at {bind_to}, {port}')
    logger.debug(f'release_all_timeout={release_all_timeout}')

    server = await loop.create_server(lambda: LiveLockProtocol(storage=storage, password=password, max_payload=max_payload), bind_to, port)

    async with server:
        await server.serve_forever()


def start(bind_to=DEFAULT_BIND_TO, port=None, release_all_timeout=None, password=None, max_payload=None):
    logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')
    asyncio.run(live_lock_server(bind_to=get_settings(bind_to, DEFAULT_BIND_TO, 'LIVELOCK_BIND_TO'),
                                 port=get_settings(port, 'LIVELOCK_PORT', DEFAULT_LIVELOCK_SERVER_PORT),
                                 release_all_timeout=get_settings(release_all_timeout, 'LIVELOCK_RELEASE_ALL_TIMEOUT', DEFAULT_RELEASE_ALL_TIMEOUT),
                                 password=get_settings(password, 'LIVELOCK_PASSWORD', None),
                                 max_payload=get_settings(max_payload, 'LIVELOCK_MAX_PAYLOAD', DEFAULT_MAX_PAYLOAD)
                                 ))
