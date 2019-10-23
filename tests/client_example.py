import os
import time

from livelock.client import LiveLock, LiveLockClientTimeoutException

os.environ['LIVELOCK_HOST'] = 'livelock.server.com'  # По умолчанию 127.0.0.1
os.environ['LIVELOCK_PASSWORD'] = 'password'  # По умолчанию None

with LiveLock('resource_id') as lock:
    time.sleep(10)

try:
    with LiveLock('resource_id', blocking=False, timeout=10) as lock:
        time.sleep(10)
except LiveLockClientTimeoutException:
    raise