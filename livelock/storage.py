from livelock.shared import DEFAULT_RELEASE_ALL_TIMEOUT

__all__ = ('LockStorage',)


class LockStorage(object):
    def __init__(self, release_all_timeout=DEFAULT_RELEASE_ALL_TIMEOUT):
        self.release_all_timeout = release_all_timeout

    def acquire(self, client_id, lock_id, reentrant):
        """
        Client with client_id attempts to acquire resource with lock_id
        :param client_id:
        :param lock_id:
        :param reentrant: return success if  client already locked resource
        :return: True if resource is acquired, False if already locked, int error code (from ERRORS) in case of error
        """
        raise NotImplemented

    def release(self, client_id, lock_id):
        """
        Regular release operation.
        Client with client_id attempt to release resource with lock_id.
        :param client_id:
        :param lock_id:
        :return: True if resource is released, False otherwise, int error code (from ERRORS) in case of error
        """
        raise NotImplemented

    def release_all(self, client_id, timeout):
        """
        Client connection lost, mark all locks as free, but save all locks at least for <timeout> seconds in case connection reestablished. (see unrelease_all)
        :param client_id:
        :param timeout: how many seconds to store locks
        :return:
        """
        raise NotImplemented

    def unrelease_all(self, client_id):
        """
        Client with existing id connected, try to restore all locks if release_all was called before for this client
        :param client_id:
        :return:
        """
        raise NotImplemented

    def locked(self, lock_id):
        """
        Check that resource with lock_id acquired by some client
        :param lock_id:
        :return: True in case resource acquired, False otherwise, int error code (from ERRORS) in case of error
        """
        raise NotImplemented

    def set_client_last_address(self, client_id, address):
        """
        Store client actual address, overwrite if exists
        :param client_id:
        :param address: Tuple of IP-address and port
        :return:
        """
        raise NotImplemented

    def get_client_last_address(self, client_id):
        """
        Find actual client address
        :param client_id:
        :return: Last address that was set by set_client_last_address
        """
        raise NotImplemented

    def find(self, pattern):
        """
        Find all acquired locks that match pattern.
        :param pattern: pattern in syntax as in fnmatch.fnmatch
        :return: list of tuples [(<lock_id>, <lock acquire timestamp>), ...], int error code (from ERRORS) in case of error
        """
        raise NotImplemented

    def add_signal(self, lock_id, signal):
        """
        Set signal is active for lock_id.
        Fails if lock_id does not acquired.
        :param lock_id:
        :param signal: string with signal id
        :return: True if signal set, int error code (from ERRORS) in case of error
        """
        raise NotImplemented

    def has_signal(self, lock_id, signal):
        """
        Check that signal is active for lock_id.
        Fails if lock_id does not acquired.
        :param lock_id:
        :param signal: string with signal id
        :return: True if signal is active, False otherwise, int error code (from ERRORS) in case of error
        """
        raise NotImplemented

    def remove_signal(self, lock_id, signal):
        """
        Deactivate signal for lock_id.
        Fails if lock_id does not acquired.
        :param lock_id:
        :param signal: string with signal id
        :return: True if signal was active and deactivated, False otherwise, int error code (from ERRORS) in case of error
        """
        raise NotImplemented

    def dump(self):
        """
        In case of non persistent storage, serialize all information about acquired locks into persistent storage.
        In case of persistent or combined storage write all cached or not persisted data into storage.
        In case of a call, you can be sure that:
        - current working directory is set to directory where you can create files
        - all operations is completed (no active acquire or release)
        This procedure can be called in case of termination or if requested by client
        :return: None
        """
        raise NotImplemented

    def load_dump(self):
        """
        Called on startup (if not disabled) to load state in case of non persistent storage.
        Current working directory is set to directory as with dump() call.
        Good practice to make maintenance on state load.
        :return:
        """
        raise NotImplemented

    def clear_dump(self):
        """
        Delete all state information.
        Current working directory is set to directory as with dump() call.
        :return:
        """

    def maintenance(self, timeout_ms):
        """
        Make maintenance.
        All incoming operations is put to wait maintenance end.
        Keep your activity short (incremental or external) because we running in asyncio loop, you have not more than <timeout> seconds (can be less than second).

        :param timeout_ms: time in milliseconds available for maintenance
        :return:
        """
        raise NotImplemented
