import logging
import socket
import time
import unittest
from contextlib import contextmanager
from multiprocessing import Process
from threading import Thread

from livelock.client import LiveLockConnection, LiveLock, LiveLockClientTimeoutException, LiveRLock

logger = logging.getLogger(__name__)


class TestLiveLock(unittest.TestCase):
    def setUp(self):
        self.network_disabled = False
        self.killed_connection = 0
        self.blocked_connection = 0

    @contextmanager
    def disable_network(self):
        _orgsocket = None

        test_instance = self

        class socksocket(socket.socket):
            def __init__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, *args, **kwargs):
                _orgsocket.__init__(self, family, type, proto, *args, **kwargs)

            def connect(self, address):
                if test_instance.network_disabled:
                    logger.debug(f'Blocking connect to {address}')
                    test_instance.blocked_connection += 1
                    raise ConnectionRefusedError
                print(f'Connection to {address}')
                return _orgsocket.connect(self, address)

        _orgsocket = socket.socket
        socket.socket = socksocket
        yield
        socket.socket = _orgsocket

    def kill_connection_worker(self):
        while True:
            time.sleep(1.2)
            if self.connection._sock:
                logger.debug('Killing connection')
                self.connection._sock.close()
                self.killed_connection += 1

    def disable_network_worker(self):
        while True:
            time.sleep((self.connection._reconnect_timeout * self.connection._reconnect_attempts) / 2)
            self.network_disabled = not self.network_disabled

    def test_high_level(self):
        logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')

        release_all_timeout = 5

        from livelock.server import start
        self.server = Process(target=start, kwargs=dict(release_all_timeout=release_all_timeout))
        self.server.start()

        self.connection2 = LiveLockConnection()

        # Base check for lock
        with LiveLock(id='1') as lock:
            self.connection = lock._connection
            self.assertTrue(lock.locked())

            # Base check for lock fail on another client
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', acquire_timeout=2, live_lock_connection=self.connection2).__enter__()

            self.assertTrue(LiveLock(id='1', live_lock_connection=self.connection2).locked())

            # Check reetrant disabled
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', acquire_timeout=2).__enter__()

            # Check reetrant lock works
            reentrant_lock = LiveRLock(id='1', acquire_timeout=2).__enter__()
            self.assertTrue(reentrant_lock.acquired)

            # Check reetrant fails on another client
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                reentrant_lock = LiveRLock(id='1', acquire_timeout=2, live_lock_connection=self.connection2).__enter__()

            # Breaking connection
            self.connection._sock.close()

            # Check that after connection lost aonther client is still cant lock resource
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', acquire_timeout=2, live_lock_connection=self.connection2).__enter__()

            time.sleep(release_all_timeout - 3)

            # Check that after connection lost another client is still cant lock resource
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', acquire_timeout=0, live_lock_connection=self.connection2).__enter__()

            # Restoring connection
            lock.ping()

            time.sleep(2)

            # Still cant lock at time after release_all_timeout passed
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', acquire_timeout=0, live_lock_connection=self.connection2).__enter__()

            # Still cant lock at time after release_all_timeout passed
            time.sleep(1)
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', acquire_timeout=0, live_lock_connection=self.connection2).__enter__()

            # First connection still has lock
            self.assertTrue(lock.locked())

            # Breaking connection and waiting until release_all_timeout
            # Then other client can lock
            self.connection._sock.close()
            time.sleep(release_all_timeout + 0.2)

            with LiveLock(id='1', acquire_timeout=0, live_lock_connection=self.connection2) as lock2:
                self.assertTrue(lock2.acquired)

                with self.assertRaises(LiveLockClientTimeoutException) as exc:
                    LiveLock(id='1', acquire_timeout=0, live_lock_connection=self.connection).__enter__()

            self.assertFalse(LiveLock(id='1', acquire_timeout=0, live_lock_connection=self.connection2).locked())


        self.server.terminate()


    def test_low_level(self):
        logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')

        release_all_timeout = 5

        from livelock.server import start
        self.server = Process(target=start, kwargs=dict(release_all_timeout=release_all_timeout))
        self.server.start()

        self.connection = LiveLockConnection()
        self.connection2 = LiveLockConnection()

        # Base check for lock
        resp = self.connection.send_command('AQ 1')
        self.assertEqual(resp, '1')

        resp = self.connection.send_command('LOCKED 1')
        self.assertEqual(resp, '1')

        # Base check for lock fail on another client
        resp = self.connection2.send_command('AQ 1')
        self.assertEqual(resp, '0')

        resp = self.connection2.send_command('LOCKED 1')
        self.assertEqual(resp, '1')

        # Check reetrant disabled
        resp = self.connection.send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Check reetrant lock works
        resp = self.connection.send_command('AQR 1')
        self.assertEqual(resp, '1')

        # Check reetrant fails on another client
        resp = self.connection2.send_command('AQR 1')
        self.assertEqual(resp, '0')

        # Breaking connection
        self.connection._sock.close()

        # Check that after connection lost aonther client is still cant lock resource
        resp = self.connection2.send_command('AQ 1')
        self.assertEqual(resp, '0')
        time.sleep(release_all_timeout - 1)

        # Check that after connection lost another client is still cant lock resource
        resp = self.connection2.send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Restoring connection
        resp = self.connection.send_command('PING')
        self.assertEqual(resp, 'PONG')

        # Still cant lock at time after release_all_timeout passed
        time.sleep(1)
        resp = self.connection2.send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Still cant lock at time after release_all_timeout passed
        time.sleep(1)
        resp = self.connection2.send_command('AQ 1')
        self.assertEqual(resp, '0')

        # First connection still has lock
        resp = self.connection.send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Breaking connection and waiting until release_all_timeout
        # Then other client can lock
        self.connection._sock.close()
        time.sleep(release_all_timeout + 0.2)

        resp = self.connection2.send_command('LOCKED 1')
        self.assertEqual(resp, '0')

        resp = self.connection2.send_command('AQ 1')
        self.assertEqual(resp, '1')

        resp = self.connection.send_command('AQ 1')
        self.assertEqual(resp, '0')

        resp = self.connection2.send_command('RELEASE 1')
        self.assertEqual(resp, '1')

        # Check situation when connection is timedout but server does not know about it
        # and second connection made from same client
        # then first connection is dropped - all locks must remain locked

        resp = self.connection.send_command('AQ 1')
        self.assertEqual(resp, '1')

        self.connection_dupe = LiveLockConnection(client_id=self.connection._client_id)
        self.connection_dupe.send_command('PING')

        # Closing connection
        self.connection._close()

        time.sleep(release_all_timeout + 1)
        # Connection is dropped but lock must stay locked
        resp = self.connection_dupe.send_command('LOCKED 1')
        self.assertEqual(resp, '1')

        resp = self.connection_dupe.send_command('RELEASE 1')
        self.assertEqual(resp, '1')

        # Random connection resets and connection blocking
        with self.disable_network():
            self.kill_thread = Thread(target=self.kill_connection_worker, daemon=True)
            self.kill_thread.start()
            self.block_thread = Thread(target=self.disable_network_worker, daemon=True)
            self.block_thread.start()

            while self.blocked_connection < 5:
                resp = self.connection.send_command('AQ 1')
                self.assertEqual(resp, '1')
                resp = self.connection2.send_command('AQ 1')
                self.assertEqual(resp, '0')

                # Not reentrant
                resp = self.connection.send_command('AQ 1')
                self.assertEqual(resp, '0')
                resp = self.connection2.send_command('AQ 1')
                self.assertEqual(resp, '0')

                time.sleep(1)

                resp = self.connection.send_command('RELEASE 1')
                # if connection reset after release command send and before answer received, then resended command returns 0
                self.assertIn(resp, ('1', '0'))

            self.server.terminate()

    def tearDown(self):
        if self.server:
            self.server.terminate()
