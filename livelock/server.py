import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from fnmatch import fnmatch

from livelock.shared import DEFAULT_RELEASE_ALL_TIMEOUT, DEFAULT_BIND_TO, DEFAULT_LIVELOCK_SERVER_PORT, get_settings, DEFAULT_MAX_PAYLOAD

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

ERRORS = {
    CONN_REQUIRED_ERROR: 'CONN required first',
    WRONG_ARGS: 'Wrong number of arguments',
    CONN_HAS_ID_ERROR: 'Already has client id',
    UNKNOWN_COMMAND_ERROR: 'Unknown command',
    PASS_ERROR: 'Wrong or no password'
}


class MemoryLockInfo(object):
    def __init__(self, id, time, ttl=None):
        self.id = id
        self.ttl = ttl
        self.time = time
        self.mark_free_after = None

    def expired(self):
        if self.mark_free_after:
            return time.time() >= self.mark_free_after
        return False


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


class CommandProtocol(asyncio.Protocol):
    command_terminator = None

    def __init__(self, max_payload, *args, **kwargs):
        self._buffer = bytearray()
        self.transport = None
        self.max_payload = max_payload
        super().__init__(*args, **kwargs)

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        self._buffer.extend(data)

        while True:
            if len(self._buffer) >= self.max_payload:
                # seems strange large request, silently close connection
                peername = self.transport.get_extra_info('peername')
                logger.debug(f'Dropping connection from {peername} with large payload {len(self._buffer)} bytes')
                self.transport.close()
                break
            index = self._buffer.find(self.command_terminator.encode())
            if index >= 0:
                command = self._buffer[0:index]
                self._buffer = self._buffer[index + 1:]
                self.on_command_received(command)
            else:
                break

    def on_command_received(self, command):
        raise NotImplemented()


class LiveLockProtocol(CommandProtocol):
    def __init__(self, storage, password, max_payload, *args, **kwargs):
        self.password = password
        self.command_terminator = '\n'
        super().__init__(max_payload=max_payload, *args, **kwargs)
        self.storage = storage
        self.client_id = None
        self._authorized = None

    def connection_made(self, transport):
        peername = transport.get_extra_info('peername')
        logger.debug(f'Connection from {peername}')
        self.transport = transport

    def connection_lost(self, exc):
        peername = self.transport.get_extra_info('peername')
        logger.debug(f'Connection lost {peername} client={self.client_id}, Exception={exc}')
        if self.client_id:
            last_address = self.storage.get_client_last_address(self.client_id)
            if last_address and last_address == peername:
                # Releasing all client locks only if last known connection is dropped
                # other old connection can be dead
                self.storage.release_all(self.client_id)

    def on_command_received(self, command):
        command = command.decode().strip()
        peername = self.transport.get_extra_info('peername')

        parts = command.split(' ')
        verb = parts[0].strip().lower()
        logger.debug(f'Got command {command} from {peername}' if verb != 'pass' else f'Got command PASS from {peername}')
        args = [x.strip() for x in parts[1:]]
        args = [x for x in args if x]

        if self.password and not self._authorized:
            if verb == 'pass':
                if len(args) == 1:
                    if args[0] == self.password:
                        self._authorized = True
                        self._reply(True)
                        return
            self._reply_error(PASS_ERROR)
            self.transport.close()

        if verb == 'conn':
            if self.client_id:
                self._reply_error(CONN_HAS_ID_ERROR)
            if args:
                self.client_id = args[0]
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
                self._reply_error(CONN_REQUIRED_ERROR)
                return
            if verb in ('aq', 'aqr'):
                if len(args) != 1:
                    self._reply_error(WRONG_ARGS)
                    return
                res = self.acquire(client_id=self.client_id, lock_id=args[0], reentrant=(verb == 'aqr'))
                self._reply(res)
            elif verb == 'release':
                if len(args) != 1:
                    self._reply_error(WRONG_ARGS)
                    return
                res = self.release(client_id=self.client_id, lock_id=args[0])
                self._reply(res)
            elif verb == 'locked':
                if len(args) != 1:
                    self._reply_error(WRONG_ARGS)
                    return
                res = self.locked(lock_id=args[0])
                self._reply(res)
            elif verb == 'ping':
                self._reply('PONG')
            elif verb == 'find':
                if len(args) != 1:
                    self._reply_error(WRONG_ARGS)
                    return
                result = list(self.storage.find(args[0]))
                self._reply_data(result)
            else:
                self._reply_error(UNKNOWN_COMMAND_ERROR)

    def _reply_error(self, code, text=None):
        if not text:
            text = ERRORS[code]
        self.transport.write(f'-{code} {text}\r\n'.encode())

    def _reply_data(self, data):
        payload = json.dumps(data, separators=(',', ':')).encode()
        self.transport.write(f'${len(payload)}\r\n'.encode())
        self.transport.write(payload)
        self.transport.write('\r\n'.encode())

    def _reply(self, content):
        self.transport.write(f'+{content}\r\n'.encode())

    def acquire(self, client_id, lock_id, reentrant):
        res = self.storage.acquire(client_id, lock_id, reentrant)
        if res:
            return '1'
        return '0'

    def release(self, client_id, lock_id):
        res = self.storage.release(client_id, lock_id)
        if res:
            return '1'
        return '0'

    def locked(self, lock_id):
        res = self.storage.locked(lock_id)
        if res:
            return '1'
        return '0'


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
