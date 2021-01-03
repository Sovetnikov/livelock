import logging
import os
import pickle
import time
from collections import defaultdict
from fnmatch import fnmatch
from random import shuffle

from livelock.shared import KEY_NOT_EXISTS
from livelock.storage import LockStorage

ABSOLUTE_PATH = lambda x: os.path.abspath(os.path.join(os.path.abspath(os.path.dirname(__file__)), x))
logger = logging.getLogger(__name__)

__all__ = ('InMemoryLockStorage',)

class MemoryLockInfo(object):
    __slots__ = ('id', 'time', 'mark_free_after', 'signals')

    def __init__(self, id, time):
        self.id = id
        self.time = time
        self.mark_free_after = None
        self.signals = None

    @property
    def expired(self):
        if self.mark_free_after:
            return time.time() >= self.mark_free_after
        return False

    def add_signal(self, signal):
        if self.signals is None:
            self.signals = set()
        self.signals.add(signal)

    def remove_signal(self, signal):
        if self.signals is not None:
            self.signals.remove(signal)

    def has_signal(self, signal):
        return self.signals is not None and signal in self.signals


class InMemoryLockStorage(LockStorage):
    def __init__(self, *args, **kwargs):
        self._clean_all_data()
        self._dump_file_name = 'livelock_memstor_dump.pickle'

        super().__init__(*args, **kwargs)

    def _clean_all_data(self):
        self.client_to_locks = defaultdict(list)
        self.locks_to_client = dict()
        self.all_locks = dict()
        self.client_last_address = dict()

    def _delete_lock(self, lock_id):
        client_id = self.locks_to_client.pop(lock_id)
        lock_info = self.all_locks.pop(lock_id)
        self.client_to_locks[client_id].remove(lock_info)


    def acquire(self, client_id, lock_id, reentrant=False):
        # Check lock expired
        lock_info = self.all_locks.get(lock_id)
        if lock_info and lock_info.expired:
            self._delete_lock(lock_id)

        locked_by = self.locks_to_client.get(lock_id)
        if locked_by:
            if reentrant and locked_by == client_id:
                # Maybe update lock time?
                return True
            return False
        self.locks_to_client[lock_id] = client_id

        lock_info = MemoryLockInfo(id=lock_id, time=time.time())
        self.client_to_locks[client_id].append(lock_info)
        self.all_locks[lock_id] = lock_info

        logger.debug('Acquire %s for %s', lock_id, client_id)
        return True

    def release(self, client_id, lock_id):
        for lock in self.client_to_locks[client_id]:
            if lock.id == lock_id:
                break
        else:
            # no such lock or client not owns it
            return False
        # break is called, client is owning this lock_id, releasing
        self._delete_lock(lock_id)
        logger.debug('Relased %s for %s', lock_id, client_id)
        return True

    def release_all(self, client_id, timeout):
        mark_free_at = time.time() + (self.release_all_timeout if not timeout else timeout)
        for lock in self.client_to_locks[client_id]:
            lock.mark_free_after = mark_free_at
        logger.debug('Marked to free at %s for %s', mark_free_at, client_id)

    def unrelease_all(self, client_id):
        for lock in self.client_to_locks[client_id]:
            lock.mark_free_after = None
        logger.debug('Restored all locks for %s', client_id)

    def locked(self, lock_id):
        lock_info = self.all_locks.get(lock_id)
        if lock_info:
            if lock_info.expired:
                self._delete_lock(lock_id)
                return False
            else:
                return True
        return False

    def set_client_last_address(self, client_id, address):
        self.client_last_address[client_id] = address

    def get_client_last_address(self, client_id):
        return self.client_last_address.get(client_id, None)

    def find(self, pattern):
        for lock_id, lock_info in self.all_locks.items():
            if lock_info.expired:
                continue
            if fnmatch(lock_id, pattern):
                yield (lock_id, lock_info.time)

    def add_signal(self, lock_id, signal):
        lock_info = self.all_locks.get(lock_id)
        if not lock_info:
            return KEY_NOT_EXISTS
        lock_info.add_signal(signal)
        return True

    def has_signal(self, lock_id, signal):
        lock_info = self.all_locks.get(lock_id)
        if not lock_info:
            return KEY_NOT_EXISTS
        return lock_info.has_signal(signal)

    def remove_signal(self, lock_id, signal):
        lock_info = self.all_locks.get(lock_id)
        if not lock_info:
            return KEY_NOT_EXISTS
        if lock_info.signals is None:
            return False
        try:
            lock_info.signals.remove(signal)
            return True
        except:
            return False

    def dump(self):
        # Dump data as fast as we can
        # All saved locks will be marked to free by timeout on dump load
        data = dict(dump_time=time.time(),
                    client_to_locks=self.client_to_locks,
                    locks_to_client=self.locks_to_client,
                    all_locks=self.all_locks,
                    client_last_address=self.client_last_address)
        logger.info('Dumping in memory lock data to %s', os.path.abspath(self._dump_file_name))
        with open(self._dump_file_name, mode='wb') as f:
            pickle.dump(data, f)
            f.flush()

    def load_dump(self):
        if os.path.isfile(self._dump_file_name):
            try:
                logger.debug('Loading in memory lock data from %s', os.path.abspath(self._dump_file_name))
                with open(self._dump_file_name, mode='rb') as f:
                    data = pickle.load(f)
                if data is None:
                    raise Exception('No data in dump')
                self.client_to_locks = data['client_to_locks']
                self.locks_to_client = data['locks_to_client']
                self.all_locks = data['all_locks']
                self.client_last_address = data['client_last_address']

                # Delete expired locks
                self.maintenance(timeout_ms=1000*60)

                for client_id in self.client_to_locks.keys():
                    self.release_all(client_id, self.release_all_timeout + 1)
                if self.client_to_locks:
                    logger.debug('Marked to free all locks after %s seconds', self.release_all_timeout + 1)
            except Exception as e:
                self._clean_all_data()
                logger.warning('Error reading dump file %s', self._dump_file_name, exc_info=True)

    def clear_dump(self):
        if os.path.isfile(self._dump_file_name):
            logger.debug('Removing in memory lock data file %s', os.path.abspath(self._dump_file_name))
            os.remove(self._dump_file_name)

    def stats(self):
        return dict(lock_count=len(self.all_locks), dump_file_path=os.path.abspath(self._dump_file_name))

    def maintenance(self, timeout_ms):
        finish_at = time.time() + timeout_ms*1000

        def finish():
            return time.time() > finish_at

        client_ids = list(self.client_to_locks)
        shuffle(client_ids)
        for client_id in client_ids:
            client_locks = list(self.client_to_locks[client_id])
            for lock in client_locks:
                if lock.expired:
                    self._delete_lock(lock.id)
            if not self.client_to_locks[client_id]:
                self.client_to_locks.pop(client_id)
                self.client_last_address.pop(client_id, None)
            if finish():
                return
