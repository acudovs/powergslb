import logging
import socket
import urllib2

import pyping
import subprocess32

from powergslb.monitor.status import get_status

import powergslb.system

__all__ = ['CheckThread']


class CheckThread(powergslb.system.AbstractThread):
    """
    PowerGSLB check thread
    """

    def __init__(self, monitor, content_id, **kwargs):
        super(CheckThread, self).__init__(**kwargs)
        self._fall = 0
        self._rise = 0
        self.monitor = monitor
        self.content_id = content_id
        self.sleep_interval = self.monitor['interval']

    def _check_fall(self):
        self._fall += 1
        self._rise = 0

        if self._fall >= self.monitor['fall'] and self.content_id not in get_status():
            logging.error('{}: {}: status fall'.format(self.name, self.monitor))
            get_status().add(self.content_id)

    def _check_rise(self):
        self._fall = 0
        self._rise += 1

        if self._rise >= self.monitor['rise'] and self.content_id in get_status():
            logging.info('{}: {}: status rise'.format(self.name, self.monitor))
            get_status().remove(self.content_id)

    def _do_exec(self):
        return subprocess32.call(self.monitor['args'], timeout=self.monitor['timeout']) == 0

    def _do_http(self):
        urllib2.urlopen(self.monitor['url'], timeout=self.monitor['timeout'])
        return True

    def _do_icmp(self):
        try:
            ip = self.monitor['ip']
            timeout = self.monitor['timeout'] * 1000
            return pyping.ping(ip, timeout, count=1).ret_code == 0
        except SystemExit:
            raise Exception('unknown host: {}'.format(self.monitor['ip']))

    def _do_tcp(self):
        address = (self.monitor['ip'], self.monitor['port'])
        socket.create_connection(address, self.monitor['timeout']).close()
        return True

    def task(self):
        try:
            if getattr(self, '_do_' + self.monitor['type'])():
                logging.debug('{}: {}: return True'.format(self.name, self.monitor))
                self._check_rise()
            else:
                logging.debug('{}: {}: return False'.format(self.name, self.monitor))
                self._check_fall()
        except Exception as e:
            logging.debug('{}: {}: return Exception: {}: {}'.format(self.name, self.monitor, type(e).__name__, e))
            self._check_fall()
