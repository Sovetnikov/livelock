import logging
import os
import random
import socket
import sys
import time
import unittest
from contextlib import contextmanager
from multiprocessing import Process
from os import _exit
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
        # Aggressive maintenance for test purposes
        os.environ['LIVELOCK_MAINTENANCE_PERIOD'] = str(0.5)

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
                LiveLock(id='1', timeout=2, live_lock_connection=self.connection2).__enter__()

            self.assertTrue(LiveLock(id='1', live_lock_connection=self.connection2).locked())

            # Check reetrant disabled
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', timeout=2).__enter__()

            # Check reetrant lock works
            reentrant_lock = LiveRLock(id='1', timeout=2).__enter__()
            self.assertTrue(reentrant_lock.acquired)

            # Check reetrant fails on another client
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                reentrant_lock = LiveRLock(id='1', timeout=2, live_lock_connection=self.connection2).__enter__()

            # Closing connection
            self.connection._sock.close()

            # Check that after connection lost another client is still cannot lock resource
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', timeout=2, live_lock_connection=self.connection2).__enter__()

            time.sleep(release_all_timeout - 4)

            # Check that after connection lost another client is still cannot lock resource
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', timeout=1, live_lock_connection=self.connection2).__enter__()

            # Restore connection
            lock.ping()

            time.sleep(2)

            # Still cant lock at time after release_all_timeout passed
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', timeout=2, live_lock_connection=self.connection2).__enter__()

            # Still cant lock at time after release_all_timeout passed
            time.sleep(1)
            with self.assertRaises(LiveLockClientTimeoutException) as exc:
                LiveLock(id='1', timeout=2, live_lock_connection=self.connection2).__enter__()

            # First connection still has lock
            self.assertTrue(lock.locked())

            # Breaking connection and waiting until release_all_timeout
            # Then other client can lock
            self.connection._sock.close()
            time.sleep(release_all_timeout + 0.2)

            id1 = LiveLock(id='1', timeout=0, live_lock_connection=self.connection2)
            with id1 as lock2:
                self.assertTrue(lock2.acquired)

                with self.assertRaises(LiveLockClientTimeoutException) as exc:
                    LiveLock(id='1', timeout=2, live_lock_connection=self.connection).__enter__()
                with LiveLock(id='prefix2', live_lock_connection=self.connection2) as lock_prefix:
                    all_result = LiveLock.find('*')
                    self.assertEqual(len(all_result), 2)
                    self.assertTrue('1' in [x['lock_id'] for x in all_result])
                    self.assertTrue('prefix2' in [x['lock_id'] for x in all_result])
                    pattern_result = LiveLock.find('prefix*')
                    self.assertTrue('prefix2' in [x['lock_id'] for x in pattern_result])

                id1.cancel()
                self.assertTrue(id1.cancelled())
            # When lock does not exists, high level api returns True on cancelled()
            self.assertTrue(id1.cancelled())
            self.assertFalse(LiveLock(id='1', timeout=0, live_lock_connection=self.connection2).locked())

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
            def command1(command):
                return self.connection.send_raw_command(command)

            def command2(command):
                return self.connection2.send_raw_command(command)
        else:
            def command1(command):
                return self.connection.send_command(*command.split(' '))

            def command2(command):
                return self.connection2.send_command(*command.split(' '))

        if password:
            with self.assertRaises(LiveLockClientException) as exc:
                command1('CONN')
            self.assertTrue('password' in str(exc.exception))

            with self.assertRaises(LiveLockClientException) as exc:
                command1('AQ 1')
            self.assertTrue('password' in str(exc.exception))

            self.connection = LiveLockConnection(password=password, port=port)

        # Test large command payload warning on client side
        # with self.assertRaises(LiveLockClientException) as exc:
        #     command1('CONN ' + 'x'*self.connection._max_payload)
        # self.assertFalse(exc.exception.code)
        # self.assertTrue('exceeded' in str(exc.exception))

        # Test large payload on server side
        # Test large command payload warning on cliend side
        # self.connection._max_payload = self.connection._max_payload*2
        # with self.assertRaises(LiveLockClientException) as exc:
        #     command1('CONN ' + 'x'*self.connection._max_payload)
        # self.assertFalse(exc.exception.code)

        # Base check for lock
        resp = command1('AQ 1')
        self.assertEqual(resp, '1')

        resp1 = command1('FIND *')
        self.assertTrue(resp1, resp1)
        self.assertEqual(resp1[0][0], '1')
        self.assertTrue(isinstance(resp1[0][1], float))
        self.assertEqual(len(resp1[0]), 2)
        self.assertEqual(len(resp1), 1)

        resp2 = command2('FIND *')
        self.assertTrue(resp2, list)
        self.assertEqual(len(resp2), 1)
        self.assertEqual(resp1[0], resp2[0])

        resp1 = command1('SIGSET 1 SIG1')
        self.assertEqual(resp1, '1')

        resp1 = command1('SIGEXISTS 1 SIG1')
        self.assertEqual(resp1, '1')

        resp1 = command1('SIGEXISTS 1 SIG3')
        self.assertEqual(resp1, '0')

        resp1 = command1('SIGSET 1 SIG1')
        self.assertEqual(resp1, '1')

        # Any client can see any signals
        resp2 = command1('SIGEXISTS 1 SIG1')
        self.assertEqual(resp2, '1')

        # Any client can set signals for any lock
        resp2 = command2('SIGSET 1 SIG2')
        self.assertEqual(resp2, '1')

        resp2 = command1('SIGEXISTS 1 SIG2')
        self.assertEqual(resp1, '1')

        resp2 = command2('SIGSET 1 SIG1')
        self.assertEqual(resp2, '1')

        # Any client can delete any signals
        resp1 = command1('SIGDEL 1 SIG2')
        self.assertEqual(resp1, '1')

        resp1 = command1('SIGDEL 1 SIG2')
        self.assertEqual(resp1, '0')

        resp = command1('LOCKED 1')
        self.assertEqual(resp, '1')

        # Base check for lock fail on another client
        resp = command2('AQ 1')
        self.assertEqual(resp, '0')

        resp = command2('LOCKED 1')
        self.assertEqual(resp, '1')

        # Check reetrant disabled
        resp = command1('AQ 1')
        self.assertEqual(resp, '0')

        # Check reetrant lock works
        resp = command1('AQR 1')
        self.assertEqual(resp, '1')

        # Check reetrant fails on another client
        resp = command2('AQR 1')
        self.assertEqual(resp, '0')

        # Breaking connection
        self.connection._sock.close()

        # Check that after connection lost another client is still cant lock resource
        resp = command2('AQ 1')
        self.assertEqual(resp, '0')

        # Not released keys is still in FIND results
        resp1 = command2('FIND *')
        self.assertTrue(resp1, list)
        self.assertEqual(len(resp1), 1)

        time.sleep(release_all_timeout - 1)

        # Check that after connection lost another client is still cant lock resource
        resp = command2('AQ 1')
        self.assertEqual(resp, '0')

        # Restoring connection
        resp = command1('PING')
        self.assertEqual(resp, 'PONG')

        # Still cant lock at time after release_all_timeout passed
        time.sleep(1)
        resp = command2('AQ 1')
        self.assertEqual(resp, '0')

        # Still cant lock at time after release_all_timeout passed
        time.sleep(1)
        resp = command2('AQ 1')
        self.assertEqual(resp, '0')

        # First connection still has lock
        resp = command1('AQ 1')
        self.assertEqual(resp, '0')

        # Breaking connection and waiting until release_all_timeout
        # Then other client can lock
        self.connection._sock.close()
        time.sleep(release_all_timeout + 0.2)

        resp = command2('LOCKED 1')
        self.assertEqual(resp, '0')

        resp = command2('AQ 1')
        self.assertEqual(resp, '1')

        resp = command1('AQ 1')
        self.assertEqual(resp, '0')

        resp = command2('RELEASE 1')
        self.assertEqual(resp, '1')

        # Check situation when connection is timedout but server does not know about it
        # and second connection made from same client
        # then first connection is dropped - all locks must remain locked

        resp = command1('AQ 1')
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

        resp = command2('AQ prefix2')
        self.assertEqual(resp, '1')

        # Two keys must be in FIND result
        resp1 = command1('FIND *')
        self.assertTrue(resp1, list)
        self.assertEqual(len(resp1), 2)

        resp2 = command2('FIND *')
        self.assertTrue(resp2, list)
        self.assertEqual(len(resp2), 2)

        resp2 = command2('FIND prefix*')
        self.assertTrue(resp2, list)
        self.assertEqual(len(resp2), 1)
        self.assertTrue('prefix2' in [x[0] for x in resp2])
        self.assertAlmostEqual([int(x[1] / 10) for x in resp2][0], int(time.time() / 10), 1)

        resp = self.connection_dupe.send_command('RELEASE', '1')
        self.assertEqual(resp, '1')

        # Random connection reset and connection blocking
        with self.disable_network():
            self.kill_thread = Thread(target=self.kill_connection_worker, daemon=True)
            self.kill_thread.start()
            self.block_thread = Thread(target=self.disable_network_worker, daemon=True)
            self.block_thread.start()

            while self.blocked_connection < 5:
                resp = command1('AQ 1')
                self.assertEqual(resp, '1')
                resp = command2('AQ 1')
                self.assertEqual(resp, '0')

                # Not reentrant
                resp = command1('AQ 1')
                self.assertEqual(resp, '0')
                resp = command2('AQ 1')
                self.assertEqual(resp, '0')

                time.sleep(1)

                resp = command1('RELEASE 1')
                # if connection reset after release command send and before answer received, then resended command returns 0
                self.assertIn(resp, ('1 0'))

            self.server.terminate()

    def tearDown(self):
        if self.server:
            self.server.terminate()

    def _start_server(self, release_all_timeout, password=None, port=None, debug=False, shutdown_support=False, log_level=None, disable_dump_load=True):
        if not port:
            port = random.randint(DEFAULT_LIVELOCK_SERVER_PORT + 1, DEFAULT_LIVELOCK_SERVER_PORT + 4)
        os.environ['LIVELOCK_PORT'] = str(port)
        if password:
            os.environ['LIVELOCK_PASSWORD'] = password

        if disable_dump_load:
            os.environ['LIVELOCK_DISABLE_DUMP_LOAD'] = '1'
        if debug:
            os.environ['LIVELOCK_DEBUG'] = '1'
        if shutdown_support:
            os.environ['LIVELOCK_SHUTDOWN_SUPPORT'] = '1'
        if log_level:
            os.environ['LIVELOCK_LOGLEVEL'] = log_level
        from livelock.server import start
        self.server = Process(target=start, kwargs=dict(release_all_timeout=release_all_timeout))
        self.server.start()

        os.environ.pop('LIVELOCK_DISABLE_DUMP_LOAD', None)
        os.environ.pop('LIVELOCK_LOGLEVEL', None)
        os.environ.pop('LIVELOCK_DEBUG', None)
        os.environ.pop('LIVELOCK_SHUTDOWN_SUPPORT', None)
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

    @unittest.skip
    def test_kv(self):
        data = 'test'
        with LiveLock('kvstore_test') as aq:
            aq.set(data)

    def test_client_fork(self):
        if sys.platform == "win32":
            # Skip test on win32
            return
        logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')

        release_all_timeout = 5
        port = self._start_server(release_all_timeout=release_all_timeout)

        configure(port=port)

        shared_lock_id = 'shared_lock'
        child_lock_id = 'child_lock'
        with LiveLock(id='fork_lock') as lock:
            # Fork in locked state with initialized live lock connection client id
            newpid = os.fork()
            if newpid == 0:
                with LiveLock(child_lock_id) as child_lock:
                    # Child process
                    for n in range(20):
                        if LiveLock(shared_lock_id).locked():
                            break
                    else:
                        _exit(1)

                    for n in range(20):
                        if LiveLock(shared_lock_id).release():
                            break
                        time.sleep(0.1)
                    _exit(0)
            else:
                with LiveLock(shared_lock_id) as shared_lock:
                    for n in range(20):
                        if LiveLock(child_lock_id).locked():
                            break
                        time.sleep(0.1)
                    else:
                        self.fail('Child lock no acquired')

                    for n in range(20):
                        if not shared_lock.locked():
                            self.fail('Shared lock released')
                        time.sleep(0.1)

    def test_bad_dump(self):
        # logging.basicConfig(level=logging.DEBUG, format='%(name)s:[%(levelname)s]: %(message)s')
        release_all_timeout = 5
        port = self._start_server(release_all_timeout=release_all_timeout, shutdown_support=True, debug=True, log_level='DEBUG', disable_dump_load=True)
        os.environ['LIVELOCK_PORT'] = str(port)
        configure(port=port)

        client = LiveLock(id='1')
        self.assertFalse(client.locked())
        self.assertTrue(client.acquire())
        stats = client._connection.send_command('STATS')
        dump_file_path = stats['dump_file_path']
        # Shutting down server, existing locks must be dumped to disk
        client._connection.send_command('SHUTDOWN')
        for n in range(4):
            if not is_port_open('127.0.0.1', port):
                break
            time.sleep(1)
        self.assertFalse(is_port_open('127.0.0.1', port))

        # Тут надо обработать ещё одну ситуацию
        # если клиент сообщил что отпустил блокировку, но сервер не получил уведомление об этом
        # то при восстановлении соединения сервер будет считать блокировку живой, а на самом деле она будет снята и она зависнет

        with self.assertRaises(ConnectionError):
            client.release(reconnect=False)
        # Resetting connection and out client id
        configure(port=port)
        del client

        # Starting new server, dumped locks must be loaded from disk and exists on server
        port = self._start_server(release_all_timeout=release_all_timeout, port=port, shutdown_support=True, debug=True, log_level='DEBUG', disable_dump_load=False)
        client = LiveLock(id='2')
        self.assertTrue(client.is_locked('1'))
        time.sleep(release_all_timeout + 1)
        # Lock must be free after timeout
        self.assertFalse(client.is_locked('1'))
        self.assertTrue(client.acquire())

        # Now dumping locks again and ruining dump file
        client._connection.send_command('SHUTDOWN')
        for n in range(4):
            if not is_port_open('127.0.0.1', port):
                break
            time.sleep(1)
        self.assertFalse(is_port_open('127.0.0.1', port))
        configure(port=port)

        self.assertTrue(os.path.exists(dump_file_path))
        with open(dump_file_path, mode='wb+') as f:
            f.write(b'0')

        # Starting server with allowed dump loading
        port = self._start_server(release_all_timeout=release_all_timeout, port=port, shutdown_support=True, debug=True, log_level='DEBUG', disable_dump_load=False)
        client = LiveLock(id='3')
        self.assertFalse(client.is_locked('2'))
