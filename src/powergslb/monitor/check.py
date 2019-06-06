import logging
import socket
import urllib2, ssl

import os

import pyping
import subprocess32

import time

from powergslb.monitor.status import get_status
import powergslb.database

import powergslb.system

#from powergslb.monitor.timeseries import RedisTimeSeries

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
        self.ts = powergslb.database.TimeSeries( **powergslb.system.get_config().items('redis') )

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
        start = time.time()
        #rt_code = subprocess32.call(self.monitor['args'], timeout=self.monitor['timeout'])
        p = Popen(self.monitor['args'],shell=True,stdin=PIPE, stdout=PIPE, stderr=STDOUT, timeout=self.monitor['timeout'])
        stdout, stderr = p.communicate()
        rt_code = out.returncode
        elapsed = time.time() - start

        logging.debug("content_id: %d - type: %s - elapsed: %f - stdout: %s", self.content_id, self.monitor['type'], elapsed, stdout )

        if 'store' in self.monitor and self.monitor['store']:
          self.ts.record_response_time( self.content_id, float(stdout) )
        else:
          self.ts.record_response_time( self.content_id, elapsed )

        return rt_code == 0

    def _do_http(self):
        if 'headers' in self.monitor and self.monitor['headers']:
          request = urllib2.Request( self.monitor['url'], headers=self.monitor['headers'] )
        else:
          request = urllib2.Request( self.monitor['url'] )

        start = time.time()
        response = urllib2.urlopen(request, timeout=self.monitor['timeout'], context=False)
        elapsed = time.time() - start

        if 'store' in self.monitor and self.monitor['store']:
          self.ts.record_response_time( self.content_id, float(response.read()) )
        else:
          self.ts.record_response_time( self.content_id, elapsed*1000.0 )

        return True

    def _do_https(self):
        secure = True
        if 'headers' in self.monitor and self.monitor['headers']:
          request = urllib2.Request( self.monitor['url'], headers=self.monitor['headers'] )
        else:
          request = urllib2.Request( self.monitor['url'] )
        if 'secure' in self.monitor and not self.monitor['secure']:
          secure = ssl._create_unverified_context()

        start = time.time()
        response = urllib2.urlopen(request, timeout=self.monitor['timeout'], context=secure)
        elapsed = time.time() - start

        if 'store' in self.monitor and self.monitor['store']:
          self.ts.record_response_time( self.content_id, float(response.read()) )
        else:
          self.ts.record_response_time( self.content_id, elapsed*1000.0 )

        return True

    def _do_icmp(self):
        try:
            ip = self.monitor['ip']
            timeout = self.monitor['timeout']

            icmp_avg_rtt = 0.0
            status = True
            if os.getuid() == 0:
              # Need to be root ;-(
              r = pyping.ping(ip, timeout * 1000, count=1).ret_code == 0
              icmp_avg_rtt = r.avg_rtt
              status = r.ret_code == 0
            else:
              command = "ping -q -c 1 -w " + str(timeout) + " " + ip + " | cut -d '/' -s -f5"
              response = subprocess32.check_output(command, shell=True, timeout=self.monitor['timeout'])
              logging.debug("command: %s - icmp avg_rtt: %s",command, response)
              icmp_avg_rtt = float(response)

            logging.debug("content_id: %d - type: %s - elapsed: %f", self.content_id, self.monitor['type'], float(icmp_avg_rtt) )
            self.ts.record_response_time( self.content_id, float(icmp_avg_rtt) )
            return status
        except SystemExit:
            raise Exception('unknown host: {}'.format(self.monitor['ip']))

    def _do_tcp(self):
        address = (self.monitor['ip'], self.monitor['port'])
        start = time.time()
        socket.create_connection(address, self.monitor['timeout']).close()
        elapsed = time.time() - start
        logging.debug("content_id: %d - type: %s - elapsed: %f", self.content_id, self.monitor['type'], elapsed*1000.0 )
        self.ts.record_response_time( self.content_id, elapsed*1000.0 )
        return True

    def task(self):
        status = True
        try:
            if getattr(self, '_do_' + self.monitor['type'])():
                logging.debug('{}: {}: return True'.format(self.name, self.monitor))
                self.ts.record_status( self.content_id, 1 )
                self._check_rise()
            else:
                logging.debug('{}: {}: return False'.format(self.name, self.monitor))
                self.ts.record_status( self.content_id, 0 )
                self._check_fall()
                status=False
        except Exception as e:
            logging.debug('{}: {}: return Exception: {}: {}'.format(self.name, self.monitor, type(e).__name__, e))
            self._check_fall()

        #logging.debug("timeserie: %s", str(self.ts.get_response_time_timeseries(self.content_id)) )
        #logging.debug("response time avg: %s", str(self.ts.get_response_time_avg(self.content_id)) )
