import os

DEFAULT_LIVELOCK_SERVER_PORT = 7873
DEFAULT_RELEASE_ALL_TIMEOUT = 5
DEFAULT_BIND_TO = '0.0.0.0'
DEFAULT_MAX_PAYLOAD = 1024
DEFAULT_SHUTDOWN_SUPPORT = False

def get_settings(value, key, default, django=False):
    if value:
        return value
    value = os.getenv(key, None)
    if not value:
        try:
            from django.core.exceptions import ImproperlyConfigured
            try:
                from django.conf import settings
                value = getattr(settings, key, None)
            except ImproperlyConfigured:
                pass
        except ImportError:
            pass
    if not value:
        value = default
    return value


def _pack_bytes(value):
    return b''.join((b'$', str(len(value)).encode(), b'\r\n', value, b'\r\n'))


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


def pack_resp(data):
    if data is None:
        return _pack_null()
    t = type(data)
    if t == str:
        return _pack_bytes(data.encode())
    elif t == bytes:
        return _pack_bytes(data)
    elif t == int:
        return _pack_int(data)
    elif t in (list, tuple):
        return _pack_array(data)
    elif t == float:
        return _pack_float(data)
    else:
        raise Exception('Unsupported type %s' % str(t))
