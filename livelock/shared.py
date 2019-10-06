import os

DEFAULT_LIVELOCK_SERVER_PORT = 7873
DEFAULT_RELEASE_ALL_TIMEOUT = 5
DEFAULT_BIND_TO = '0.0.0.0'
DEFAULT_MAX_PAYLOAD = 1024

def get_settings(value, key, default):
    if value:
        return value
    value = os.getenv(key, None)
    if not value:
        try:
            from django.conf import settings
            value = getattr(settings, key, None)
        except ImportError:
            pass
    if not value:
        value = default
    return value
