import json
import logging
import os
import random
import socket
import time
import unittest
from contextlib import contextmanager
from multiprocessing import Process
from threading import Thread

from livelock.client import LiveLockConnection, LiveLock, LiveLockClientTimeoutException, LiveRLock, LiveLockClientException, configure
from livelock.shared import DEFAULT_LIVELOCK_SERVER_PORT

logger = logging.getLogger(__name__)

import logging


def is_port_open(host, port, timeout=5):
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    result = sock.connect_ex((host, int(port)))
    return result == 0


class TestLiveLock(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        self.server = None
        super().__init__(*args, **kwargs)

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

    def test_high_level_password(self, password=None):
        self.test_high_level(password=str(random.randint(10000000, 99999999)))

    def test_basic(self, password=None):
        logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')

        release_all_timeout = 5
        port = self._start_server(release_all_timeout=release_all_timeout, password=password)

        os.environ['LIVELOCK_PORT'] = str(port)
        if password:
            os.environ['LIVELOCK_PASSWORD'] = password

        configure(port=port, password=password)

        with LiveLock(id='1') as lock:
            time.sleep(10)

    def test_high_level(self, password=None):
        logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')

        release_all_timeout = 5
        port = self._start_server(release_all_timeout=release_all_timeout, password=password)
        self.connection2 = LiveLockConnection(port=port, password=password)

        os.environ['LIVELOCK_PORT'] = str(port)
        if password:
            os.environ['LIVELOCK_PASSWORD'] = password

        # Reset default connection from another test cases
        configure(port=port, password=password)

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
                with LiveLock(id='prefix2', live_lock_connection=self.connection2) as lock_prefix:
                    all_result = LiveLock.find('*')
                    self.assertEqual(len(all_result), 2)
                    self.assertTrue('1' in [x[0] for x in all_result])
                    self.assertTrue('prefix2' in [x[0] for x in all_result])
                    pattern_result = LiveLock.find('prefix*')
                    self.assertTrue('prefix2' in [x[0] for x in pattern_result])

            self.assertFalse(LiveLock(id='1', acquire_timeout=0, live_lock_connection=self.connection2).locked())

        self.server.terminate()

    def test_low_level_password(self):
        self.test_low_level(password=str(random.randint(10000000, 99999999)))

    def test_low_level_raw(self):
        self.test_low_level(raw=True)

    def test_low_level(self, password=None, raw=False):
        logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')

        release_all_timeout = 5
        port = self._start_server(release_all_timeout=release_all_timeout, password=password)

        self.connection = LiveLockConnection(port=port)
        self.connection2 = LiveLockConnection(password=password, port=port)

        if raw:
            def self_connection_send_command(command):
                return self.connection.send_raw_command(command)

            def self_connection2_send_command(command):
                return self.connection2.send_raw_command(command)
        else:
            def self_connection_send_command(command):
                return self.connection.send_command(*command.split(' '))

            def self_connection2_send_command(command):
                return self.connection2.send_command(*command.split(' '))

        if password:
            with self.assertRaises(LiveLockClientException) as exc:
                self_connection_send_command('CONN')
            self.assertTrue('password' in str(exc.exception))

            with self.assertRaises(LiveLockClientException) as exc:
                self_connection_send_command('AQ 1')
            self.assertTrue('password' in str(exc.exception))

            self.connection = LiveLockConnection(password=password, port=port)

        # Test large command payload warning on cliend side
        # with self.assertRaises(LiveLockClientException) as exc:
        #     self_connection_send_command('CONN ' + 'x'*self.connection._max_payload)
        # self.assertFalse(exc.exception.code)
        # self.assertTrue('exceeded' in str(exc.exception))

        # Test large payload on server side
        # Test large command payload warning on cliend side
        # self.connection._max_payload = self.connection._max_payload*2
        # with self.assertRaises(LiveLockClientException) as exc:
        #     self_connection_send_command('CONN ' + 'x'*self.connection._max_payload)
        # self.assertFalse(exc.exception.code)

        # Base check for lock
        resp = self_connection_send_command('AQ 1')
        self.assertEqual(resp, '1')

        resp1 = self_connection_send_command('FIND *')
        self.assertTrue(resp1, resp1)
        self.assertEqual(resp1[0][0], b'1')
        self.assertTrue(isinstance(resp1[0][1], float))
        self.assertEqual(len(resp1[0]), 2)
        self.assertEqual(len(resp1), 1)

        resp2 = self_connection2_send_command('FIND *')
        self.assertTrue(resp2, list)
        self.assertEqual(len(resp2), 1)
        self.assertEqual(resp1[0], resp2[0])

        # FIND all clients can find all keys
        resp = self_connection_send_command('LOCKED 1')
        self.assertEqual(resp, '1')

        # Base check for lock fail on another client
        resp = self_connection2_send_command('AQ 1')
        self.assertEqual(resp, '0')

        resp = self_connection2_send_command('LOCKED 1')
        self.assertEqual(resp, '1')

        # Check reetrant disabled
        resp = self_connection_send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Check reetrant lock works
        resp = self_connection_send_command('AQR 1')
        self.assertEqual(resp, '1')

        # Check reetrant fails on another client
        resp = self_connection2_send_command('AQR 1')
        self.assertEqual(resp, '0')

        # Breaking connection
        self.connection._sock.close()

        # Check that after connection lost another client is still cant lock resource
        resp = self_connection2_send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Not released keys is still in FIND results
        resp1 = self_connection2_send_command('FIND *')
        self.assertTrue(resp1, list)
        self.assertEqual(len(resp1), 1)

        time.sleep(release_all_timeout - 1)

        # Check that after connection lost another client is still cant lock resource
        resp = self_connection2_send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Restoring connection
        resp = self_connection_send_command('PING')
        self.assertEqual(resp, 'PONG')

        # Still cant lock at time after release_all_timeout passed
        time.sleep(1)
        resp = self_connection2_send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Still cant lock at time after release_all_timeout passed
        time.sleep(1)
        resp = self_connection2_send_command('AQ 1')
        self.assertEqual(resp, '0')

        # First connection still has lock
        resp = self_connection_send_command('AQ 1')
        self.assertEqual(resp, '0')

        # Breaking connection and waiting until release_all_timeout
        # Then other client can lock
        self.connection._sock.close()
        time.sleep(release_all_timeout + 0.2)

        resp = self_connection2_send_command('LOCKED 1')
        self.assertEqual(resp, '0')

        resp = self_connection2_send_command('AQ 1')
        self.assertEqual(resp, '1')

        resp = self_connection_send_command('AQ 1')
        self.assertEqual(resp, '0')

        resp = self_connection2_send_command('RELEASE 1')
        self.assertEqual(resp, '1')

        # Check situation when connection is timedout but server does not know about it
        # and second connection made from same client
        # then first connection is dropped - all locks must remain locked

        resp = self_connection_send_command('AQ 1')
        self.assertEqual(resp, '1')

        self.connection_dupe = LiveLockConnection(client_id=self.connection._client_id, port=port, password=password)
        self.connection_dupe.send_command('PING')

        # Closing connection
        self.connection._close()

        time.sleep(release_all_timeout + 1)
        with self.assertRaises(LiveLockClientException) as exc:
            # text commands not supported by server when encoded in RESP
            self.connection_dupe.send_command('LOCKED 1')
        self.assertTrue('Unknown command' in str(exc.exception))

        # Connection is dropped but lock must stay locked
        resp = self.connection_dupe.send_command('LOCKED', '1')
        self.assertEqual(resp, '1')

        resp = self_connection2_send_command('AQ prefix2')
        self.assertEqual(resp, '1')

        # Two keys must be in FIND result
        resp1 = self_connection_send_command('FIND *')
        self.assertTrue(resp1, list)
        self.assertEqual(len(resp1), 2)

        resp2 = self_connection2_send_command('FIND *')
        self.assertTrue(resp2, list)
        self.assertEqual(len(resp2), 2)

        resp2 = self_connection2_send_command('FIND prefix*')
        self.assertTrue(resp2, list)
        self.assertEqual(len(resp2), 1)
        self.assertTrue(b'prefix2' in [x[0] for x in resp2])
        self.assertAlmostEqual([int(x[1] / 10) for x in resp2][0], int(time.time() / 10), 1)

        resp = self.connection_dupe.send_command('RELEASE', '1')
        self.assertEqual(resp, '1')

        # Random connection resets and connection blocking
        with self.disable_network():
            self.kill_thread = Thread(target=self.kill_connection_worker, daemon=True)
            self.kill_thread.start()
            self.block_thread = Thread(target=self.disable_network_worker, daemon=True)
            self.block_thread.start()

            while self.blocked_connection < 5:
                resp = self_connection_send_command('AQ 1')
                self.assertEqual(resp, '1')
                resp = self_connection2_send_command('AQ 1')
                self.assertEqual(resp, '0')

                # Not reentrant
                resp = self_connection_send_command('AQ 1')
                self.assertEqual(resp, '0')
                resp = self_connection2_send_command('AQ 1')
                self.assertEqual(resp, '0')

                time.sleep(1)

                resp = self_connection_send_command('RELEASE 1')
                # if connection reset after release command send and before answer received, then resended command returns 0
                self.assertIn(resp, ('1', '0'))

            self.server.terminate()

    def tearDown(self):
        if self.server:
            self.server.terminate()

    def _start_server(self, release_all_timeout, password=None, port=None):
        if not port:
            port = random.randint(DEFAULT_LIVELOCK_SERVER_PORT + 1, DEFAULT_LIVELOCK_SERVER_PORT + 4)
        os.environ['LIVELOCK_PORT'] = str(port)
        if password:
            os.environ['LIVELOCK_PASSWORD'] = password

        from livelock.server import start
        self.server = Process(target=start, kwargs=dict(release_all_timeout=release_all_timeout))
        self.server.start()

        os.environ.pop('LIVELOCK_PORT')
        if password:
            os.environ.pop('LIVELOCK_PASSWORD')
        for n in range(10):
            print('Waiting server process PID %s (exitcode=%s)' % (self.server.pid, self.server.exitcode))
            if is_port_open('127.0.0.1', port):
                break
            time.sleep(1)
        self.assertTrue(is_port_open('127.0.0.1', port))
        return port
