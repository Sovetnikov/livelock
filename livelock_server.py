import os

from livelock.server import start

if __name__ == '__main__':
    SENTRY_DSN = os.environ.get("SENTRY_DSN")
    if SENTRY_DSN:
        import sentry_sdk
        sentry_sdk.init(dsn=SENTRY_DSN)

    start()
