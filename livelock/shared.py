import os

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
DEFAULT_TCP_USER_TIMEOUT_SECONDS = DEFAULT_TCP_KEEPALIVE_TIME + DEFAULT_TCP_KEEPALIVE_INTERVAL*DEFAULT_TCP_KEEPALIVE_PROBES
DEFAULT_DISABLE_DUMP_LOAD = False

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
