import logging
import os
import socket
import threading
import time

from livelock.connection import SocketBuffer
from livelock.server import KEY_NOT_EXISTS
from livelock.shared import get_settings, DEFAULT_MAX_PAYLOAD, pack_resp

logger = logging.getLogger(__name__)

threadLocal = threading.local()


def configure(host=None, port=None, password=None):
    existing_connection = getattr(threadLocal, 'live_lock_connection', None)
    if existing_connection:
        existing_connection._close()
    setattr(threadLocal, 'live_lock_connection', LiveLockConnection(host=host, port=port, password=password))


def _get_connection():
    existing_connection = getattr(threadLocal, 'live_lock_connection', None)
    if not existing_connection:
        configure()
    existing_connection = getattr(threadLocal, 'live_lock_connection', None)
    if not existing_connection:
        raise LiveLockClientException('Cant create connection')
    return existing_connection


class LiveLockClientException(Exception):
    def __init__(self, msg, code=None):
        self.code = int(code) if code else None
        super().__init__(msg)


class LiveLockClientTimeoutException(LiveLockClientException):
    pass


class LiveLockConnection(object):
    def __init__(self, host=None, port=None, client_id=None, password=None, max_payload=None):
        self.host = get_settings(host, 'LIVELOCK_HOST', '127.0.0.1', django=True)

        from livelock.shared import DEFAULT_LIVELOCK_SERVER_PORT
        port = get_settings(port, 'LIVELOCK_PORT', DEFAULT_LIVELOCK_SERVER_PORT, django=True)
        try:
            port = int(port)
        except:
            raise Exception('Live lock server port is not integer: ' + str(port))

        self.port = port

        self._password = get_settings(password, 'LIVELOCK_PASSWORD', None, django=True)
        self._max_payload = get_settings(max_payload, 'LIVELOCK_MAX_PAYLOAD', DEFAULT_MAX_PAYLOAD, django=True)
        self._sock = None
        self._buffer = None

        self._reconnect_timeout = 1
        self._reconnect_attempts = 3
        
        self.client_id = client_id
        self._sock_pid = os.getpid()
    
    @property
    def client_id(self):
        if self._client_id_pid == os.getpid():
            return self._client_id
        return None

    @client_id.setter
    def client_id(self, client_id):
        self._client_id = client_id
        self._client_id_pid = os.getpid()

    def _close(self):
        if self._sock and self._sock_pid == os.getpid():
            self._sock.close()
        self._sock = None
        self._buffer = None

    def _connect(self, reconnect=True):
        if not self._sock or self._sock_pid != os.getpid():
            if not self._sock:
                logger.info('No socket, connecting')
            if self._sock_pid and self._sock_pid != os.getpid():
                logger.info('Process PID changed, connecting')
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            x = sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
            if (x == 0):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            reconnect_attempts = self._reconnect_attempts
            while reconnect_attempts:
                try:
                    sock.connect((self.host, self.port))
                    break
                except ConnectionRefusedError as e:
                    reconnect_attempts -= 1
                    if not reconnect_attempts:
                        raise e
                    time.sleep(self._reconnect_timeout)

            self._sock = sock
            self._sock_pid = os.getpid()
            self._buffer = SocketBuffer(sock, 65536)
            self._send_connect(reconnect=reconnect)

    def _reconnect(self):
        if self._sock and self._sock_pid == os.getpid():
            self._sock.close()
        self._sock = None
        # This proc called from connection loop, do not reconnect on inner commands fail
        self._connect(reconnect=False)

    def _send_connect(self, reconnect=True):
        if self._password:
            resp = self.send_command('PASS', self._password, reconnect=False)

        if self.client_id:
            client_id = self.send_command('CONN', self.client_id, reconnect=reconnect)
            if client_id != self.client_id:
                raise Exception('client_id ({client_id}) != self.client_id ({self.client_id})'.format(**locals()))
        else:
            client_id = self.send_command('CONN', reconnect=reconnect)
            self.client_id = client_id
        self.send_command('CONNINFO', '%s:%s' % (socket.gethostname(), os.getpid()), reconnect=reconnect)

    def _read_int(self):
        line = self._buffer.readline()
        return int(line.decode().strip())

    def _read_float(self):
        line = self._buffer.readline()
        return float(line.decode().strip())

    def _read_bytes(self):
        len = self._read_int()
        line = self._buffer.read(max(2, len + 2))
        if line[-1] != ord(b'\n'):
            raise Exception(r"line[-1] != ord(b'\n')")
        if len < 0:
            return None
        if len == 0:
            return b''
        return line[:-2]

    def _read_array(self):
        len = self._read_int()
        r = []
        while len:
            c = self._buffer.read(1)
            value = self._receive_resp(c)
            r.append(value)
            len -= 1
        return r

    def _receive_resp(self, c):
        if c == b':':
            return self._read_int()
        elif c == b'$':
            return self._read_bytes()
        elif c == b'*':
            return self._read_array()
        elif c == b',':
            return self._read_float()
        else:
            raise Exception('Unknown RESP start char %s' % c)

    def _read_response(self):
        c = self._buffer.read(1)
        if c in b':*$,':
            value = self._receive_resp(c)
            return value
        elif c == b'+':
            response = self._buffer.readline()
            data = response.decode()
            return data
        elif c == b'-':
            response = self._buffer.readline()
            data = response.decode()
            code = data.split(' ')[0]
            msg = data.replace(code, '').strip()
            code.strip('-')
            raise LiveLockClientException(msg, code)
        else:
            raise LiveLockClientException('Unknown char in RESP response start %s' % c)

    def send_raw_command(self, command, reconnect=True):
        payload = command.encode() + b'\r\n'
        return self._send_command(payload, reconnect=reconnect)

    def send_command(self, command, *args, reconnect=True):
        payload = pack_resp(([command, ] + list(args)) if args else [command, ])
        return self._send_command(payload, reconnect=reconnect)

    def _send_command(self, payload, reconnect=True):
        self._connect()

        data = None
        send_success = None
        reconnect_attempts = self._reconnect_attempts

        payload_len = len(payload)
        if payload_len > self._max_payload + 1:
            raise LiveLockClientException('Max command payload size exceeded {payload_len}b with limit of {self._max_payload}b'.format(**locals()))
        reconnect_phase = False

        while reconnect_attempts:
            try:
                if reconnect_phase:
                    self._reconnect()
                    reconnect_phase = False
                # if connection lost on AQ command, lock can be acquired by server but we can lost success response from server
                self._sock.sendall(payload)
                send_success = True
                data = self._read_response()
                break
            except (ConnectionResetError, OSError, ConnectionError) as e:
                logger.info('Got exception on send_command: %s' % e)
                reconnect_attempts -= 1
                if not reconnect or not reconnect_attempts:
                    raise e
                logger.info('Got connection error, reconnecting')
                # No sleep on first reconnect attempt
                if reconnect_attempts != self._reconnect_attempts - 1:
                    time.sleep(self._reconnect_timeout)
                # if send_success:
                    # FIXME: if AQ command sended but answer is not received make AQR on next try
                    # pass
                # Maybe check that locked resources still locked and relock if necessary (in case lock server restarted)
                reconnect_phase = True
        return data


class LiveLock(object):
    def __init__(self, id, blocking=True, timeout=-1, live_lock_connection=None):
        if live_lock_connection is None:
            live_lock_connection = _get_connection()
        self._connection = live_lock_connection
        self.id = id
        self.acquired = False
        self.retry_interval = 1
        self.timeout = timeout
        self.reentrant = False
        self.blocking = blocking

    @classmethod
    def find(cls, pattern):
        connection = _get_connection()
        data = connection.send_command('FIND', pattern)
        for row in data:
            # decoding lock ID
            row[0] = row[0].decode()
        return data

    def acquire(self, blocking=None):
        if blocking is None:
            blocking = self.blocking

        if blocking is True:
            deadline = time.monotonic() + self.timeout
            # In case of low or zero timeout
            if self._acquire():
                return True

            while self.timeout < 0 or time.monotonic() < deadline:
                sleep_time = deadline - time.monotonic()
                if sleep_time < self.retry_interval:
                    # seems last lock acquire try
                    sleep_time = sleep_time / 2
                else:
                    sleep_time = self.retry_interval
                time.sleep(sleep_time)

                if self._acquire():
                    return True

            raise LiveLockClientTimeoutException('Timeout elapsed after %s seconds '
                                                 'while trying to acquire '
                                                 'lock.' % self.timeout)
        else:
            return self._acquire()

    def __enter__(self):
        self.acquired = self.acquire(blocking=self.blocking)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.acquired:
            self.release()

    def __del__(self):
        try:
            if self.acquired:
                self.release(reconnect=False)
        except:
            pass

    def _acquire(self):
        command = 'AQR' if self.reentrant else 'AQ'
        resp = self._connection.send_command(command, self.id)
        return resp == '1'

    def release(self, reconnect=True):
        resp = self._connection.send_command('RELEASE', self.id, reconnect=reconnect)
        self.acquired = False
        return resp == '1'

    def locked(self):
        resp = self._connection.send_command('LOCKED', self.id)
        return resp == '1'

    def ping(self):
        self._connection.send_command('PING')

    def signal(self, signal):
        resp = self._connection.send_command('SIGSET', self.id, signal)
        return resp == '1'

    def remove_signal(self, signal):
        resp = self._connection.send_command('SIGDEL', self.id, signal)
        return resp == '1'

    def signal_exists(self, signal):
        resp = self._connection.send_command('SIGEXISTS', self.id, signal)
        return resp == '1'

    def cancel(self):
        return self.signal('CANCEL')

    def cancelled(self):
        try:
            return self.signal_exists('CANCEL')
        except LiveLockClientException as e:
            if e.code == KEY_NOT_EXISTS:
                return True
            raise e


class LiveRLock(LiveLock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reentrant = True
