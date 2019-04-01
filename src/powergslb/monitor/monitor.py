import ast
import logging
import time

from powergslb.monitor.check import CheckThread
from powergslb.monitor.status import get_status, init_status

import powergslb.database
import powergslb.system

__all__ = ['MonitorThread']


class MonitorThread(powergslb.system.AbstractThread):
    """
    PowerGSLB monitor thread
    """
    _check_params_types = {
        'exec': {'type': str, 'args': list, 'interval': int, 'timeout': int, 'fall': int, 'rise': int},
        'icmp': {'type': str, 'ip': str, 'interval': int, 'timeout': int, 'fall': int, 'rise': int},
        'http': {'type': str, 'url': str, 'interval': int, 'timeout': int, 'fall': int, 'rise': int},
        'tcp': {'type': str, 'ip': str, 'port': int, 'interval': int, 'timeout': int, 'fall': int, 'rise': int}
    }

    def __init__(self, **kwargs):
        super(MonitorThread, self).__init__(**kwargs)
        self._check_threads = []
        self._checks = []
        self._refresh_threads = False
        self.sleep_interval = powergslb.system.get_config().get('monitor', 'update_interval')

        init_status()

    def _clean_status(self):
        check_ids = set(check['id'] for check in self._checks)
        stale_ids = get_status().difference(check_ids)

        if stale_ids:
            logging.debug('{}: clean status for records: {}'.format(
                type(self).__name__, ', '.join(map(str, stale_ids))))
            get_status().intersection_update(check_ids)

    def _parse(self, check):
        parse_status = False
        try:
            check['monitor_json'] = dict(ast.literal_eval(check['monitor_json'] % check))
            parse_status = True
        except (SyntaxError, ValueError) as e:
            logging.error('{}: content id {}: check parsing error: {}: {}'.format(
                type(self).__name__, check['id'], type(e).__name__, e))

        return parse_status

    def _shutdown_check_threads(self):
        if not self._check_threads:
            logging.info('{}: check threads are not running'.format(type(self).__name__))
            return

        logging.debug('{}: shutdown threads: {}'.format(type(self).__name__, self._check_threads))

        alive_threads = []
        shutdown_timeout = 0

        for check_thread in self._check_threads:
            if check_thread.is_alive():
                check_thread.shutdown()
                alive_threads.append(check_thread)
                if check_thread.sleep_interval > shutdown_timeout:
                    shutdown_timeout = check_thread.sleep_interval

        shutdown_time = time.time()
        shutdown_timeout *= 2

        while alive_threads and time.time() - shutdown_time < shutdown_timeout:
            time.sleep(1)
            alive_threads = [thread for thread in alive_threads if thread.is_alive()]

        self._check_threads = alive_threads

    def _start_check_threads(self):
        if self._check_threads:
            logging.error('{}: check threads already running: {}'.format(type(self).__name__, self._check_threads))
            return

        check_threads = []
        for check in self._checks:
            thread_name = 'Check-{}'.format(check['id'])
            check_thread = CheckThread(check['monitor_json'], check['id'], name=thread_name)
            check_thread.start()
            check_threads.append(check_thread)

        logging.debug('{}: started threads: {}'.format(type(self).__name__, check_threads))

        self._check_threads = check_threads

    def _update_checks(self):
        logging.info('{}: update checks from the database'.format(type(self).__name__))
        refresh_threads = False
        try:
            with powergslb.database.Database(**powergslb.system.get_config().items('database')) as database:
                raw_checks = database.gslb_checks()
        except powergslb.database.Database.Error as e:
            logging.error('{}: {}: {}'.format(type(self).__name__, type(e).__name__, e))
        else:
            checks = [check for check in raw_checks if self._parse(check) and self._validate(check)]
            if self._checks != checks:
                logging.debug('{}: checks updated: {}'.format(type(self).__name__, checks))
                self._checks = checks
                refresh_threads = True

        self._refresh_threads = refresh_threads

    def _validate(self, check):
        validate_status = False
        try:
            monitor_type = check['monitor_json']['type']

            if not monitor_type:
                return validate_status

            check_params = set(self._check_params_types[monitor_type])
            monitor_params = set(check['monitor_json'])

            if check_params != monitor_params:
                missing_params = check_params.difference(monitor_params)
                unexpected_params = monitor_params.difference(check_params)

                if missing_params:
                    raise Exception("{}: content id {}: missing check parameters: {}".format(
                        type(self).__name__, check['id'], ', '.join(map(str, missing_params))))

                if unexpected_params:
                    raise Exception("{}: content id {}: unexpected check parameters: {}".format(
                        type(self).__name__, check['id'], ', '.join(map(str, unexpected_params))))

            for param, param_type in self._check_params_types[monitor_type].items():
                if type(check['monitor_json'][param]) != param_type:
                    raise Exception("{}: content id {}: check parameter '{}' invalid".format(
                        type(self).__name__, check['id'], param))

        except KeyError as e:
            logging.error("{}: content id {}: check parameter '{}' missing".format(
                type(self).__name__, check['id'], e.message))

        except Exception as e:
            logging.error(e)
        else:
            if check['monitor_json']['timeout'] > check['monitor_json']['interval']:
                logging.warning("{}: content id {}: check 'timeout' is greater than 'interval': fixed".format(
                    type(self).__name__, check['id']))
                check['monitor_json']['timeout'] = check['monitor_json']['interval']

            validate_status = True

        return validate_status

    def _verify_check_threads(self):
        if self._refresh_threads:
            return

        check_ids = set(check['id'] for check in self._checks)
        check_thread_ids = set(thread.content_id for thread in self._check_threads if thread.is_alive())

        if check_ids != check_thread_ids:
            running_thread_ids = check_thread_ids.difference(check_ids)
            stopped_thread_ids = check_ids.difference(check_thread_ids)

            if running_thread_ids:
                logging.error('{}: unexpectedly running threads: {}'.format(
                    type(self).__name__, ', '.join(map('Check-{}'.format, running_thread_ids))))

            if stopped_thread_ids:
                logging.error('{}: unexpectedly stopped threads: {}'.format(
                    type(self).__name__, ', '.join(map('Check-{}'.format, stopped_thread_ids))))

            self._refresh_threads = True

    def task(self):
        self._update_checks()
        self._verify_check_threads()
        if self._refresh_threads:
            self._shutdown_check_threads()
            self._clean_status()
            self._start_check_threads()
