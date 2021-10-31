import logging
import os
logger = logging.getLogger(__name__)

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

DEFAULT_LIVELOCK_SERVER_PORT = 7873
DEFAULT_RELEASE_ALL_TIMEOUT = 5
DEFAULT_BIND_TO = '0.0.0.0'
DEFAULT_MAX_PAYLOAD = 1024
DEFAULT_SHUTDOWN_SUPPORT = False
DEFAULT_PROMETHEUS_PORT = None
DEFAULT_TCP_KEEPALIVE_TIME = 30  # seconds
DEFAULT_TCP_KEEPALIVE_INTERVAL = 10  # seconds
DEFAULT_TCP_KEEPALIVE_PROBES = 10
DEFAULT_LOGLEVEL = 'INFO'
DEFAULT_TCP_USER_TIMEOUT_SECONDS = DEFAULT_TCP_KEEPALIVE_TIME + DEFAULT_TCP_KEEPALIVE_INTERVAL * DEFAULT_TCP_KEEPALIVE_PROBES
DEFAULT_DISABLE_DUMP_LOAD = False
DEFAULT_MAINTENANCE_TIMEOUT_MS = 50  # milliseconds
DEFAULT_MAINTENANCE_PERIOD = 10  # seconds
DEFAULT_LIVELOCK_ACQUIRE_TIMEOUT = 60 # seconds

def get_settings(value, key, default):
    if value is not None:
        return value
    value = os.getenv(key, None)
    if value is None:
        try:
            from django.core.exceptions import ImproperlyConfigured
            try:
                from django.conf import settings
                value = getattr(settings, key, None)
            except ImproperlyConfigured:
                pass
        except ImportError:
            pass
    if value is None:
        value = default
    return value

def get_int_settings(value, key, default):
    v = get_settings(value, key, default)
    if v is not None and not isinstance(v, float):
        try:
            return int(v)
        except:
            logger.exception('Can\'t convert settings value "%s" for "%s" into int %s', value, key)
    return default

def get_float_settings(value, key, default):
    v = get_settings(value, key, default)
    if v is not None and not isinstance(v, float):
        try:
            return float(v)
        except:
            logger.exception('Can\'t convert settings value "%s" for "%s" into float %s', value, key)
    return default

def _pack_bytes(value):
    return b''.join((b'^', str(len(value)).encode(), b'\r\n', value, b'\r\n'))


def _pack_blob_str(value):
    value = value.encode()
    return b''.join((b'$', str(len(value)).encode(), b'\r\n', value, b'\r\n'))


def _pack_str(value):
    value = value.encode()
    return b''.join((b'+', value, b'\r\n', value, b'\r\n'))


def _pack_null():
    return b'$-1\r\n'


def _pack_int(value):
    return b''.join((b':', str(value).encode(), b'\r\n'))


def _pack_float(value):
    return b''.join((b',', str(value).encode(), b'\r\n'))


def _pack_array(value):
    r = [pack_resp(x) for x in value]
    l = len(r)
    r.insert(0, b''.join((b'*', str(l).encode(), b'\r\n')))
    return b''.join(r)


def _pack_dict(value):
    r = []
    for k, v in value.items():
        if type(k) not in (tuple, list, int, float, str):
            raise Exception('Dict keys of type %s not supported' % type(k))
        r.append(pack_resp(k))
        r.append(pack_resp(v))
    l = len(value)
    r.insert(0, b''.join((b'%', str(l).encode(), b'\r\n')))
    return b''.join(r)


def _pack_bool(value):
    if value:
        return b'#t\r\n'
    return b'#f\r\n'


def pack_resp(data):
    if data is None:
        return _pack_null()
    t = type(data)
    if t == str:
        return _pack_blob_str(data)
    elif t == int:
        return _pack_int(data)
    elif t in (list, tuple):
        return _pack_array(data)
    elif t == dict:
        return _pack_dict(data)
    elif t == float:
        return _pack_float(data)
    elif t == bool:
        return _pack_bool(data)
    elif t == bytes:
        return _pack_bytes(data)
    else:
        raise Exception('Unsupported type %s' % str(t))

class LazyArg(object):
    __slots__ = ('action',)

    def __init__(self, action):
        self.action = action

    def __str__(self):
        return str(self.action())