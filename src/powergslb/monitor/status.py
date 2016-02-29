import threading

__all__ = ['ThreadSafeSet', 'get_status', 'init_status']

__status = None


class ThreadSafeSet(set):
    """
    Thread-safe set locks all public attributes
    """

    def __init__(self, seq=()):
        super(ThreadSafeSet, self).__init__(seq)
        self.__lock = threading.RLock()

    def __getattribute__(self, name):
        if not name.startswith('_'):
            with self.__lock:
                return super(ThreadSafeSet, self).__getattribute__(name)
        else:
            return super(ThreadSafeSet, self).__getattribute__(name)


def init_status():
    global __status
    __status = ThreadSafeSet()


def get_status():
    return __status
