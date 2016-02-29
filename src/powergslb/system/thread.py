import abc
import logging
import threading
import time

__all__ = ['AbstractThread']


class AbstractThread(threading.Thread):
    """
    Abstract thread
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, **kwargs):
        super(AbstractThread, self).__init__(**kwargs)
        self.__shutdown_event = threading.Event()
        self.__shutdown_request = False
        self.daemon = True
        self.sleep_interval = 0

    def run(self):
        logging.debug('{} thread started'.format(self.name))
        try:
            while not self.__shutdown_request:
                self.task()
                time.sleep(self.sleep_interval)
        finally:
            logging.debug('{} thread stopped'.format(self.name))
            self.__shutdown_event.set()

    def shutdown(self, timeout=0):
        logging.debug('{} thread shutdown'.format(self.name))
        self.__shutdown_request = True
        self.__shutdown_event.wait(timeout)

    @abc.abstractmethod
    def task(self):
        pass
