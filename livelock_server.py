import logging
import os

from livelock.server import start

logger = logging.getLogger(__name__)

if __name__ == '__main__':
    SENTRY_DSN = os.environ.get("SENTRY_DSN")
    if SENTRY_DSN:
        logger.debug('Turning ON sentry')
        import sentry_sdk

        sentry_sdk.init(dsn=SENTRY_DSN)

    start()
