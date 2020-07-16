import logging

import redis
from kairos import Timeseries

__all__ = ['RedisTimeSeries']


class RedisTimeSeries:

    def __init__(self, **kwargs):
        self.client = None
        try:
            self.client = redis.StrictRedis(host=kwargs['host'], port=kwargs['port'], db=kwargs['db'])
            self.client.ping()
            logging.debug('Redis host=%s,port=%s,db=%d- Connected!', kwargs['host'], kwargs['port'], kwargs['db'])
        except Exception as ex:
            self.client = None
            logging.error("Redis host=%s,port=%s,db=%d- Error %s", ex, kwargs['host'], kwargs['port'], kwargs['db'])
            pass

        self.ts = None
        if self.client is not None:
            logging.debug('Timeseries - Create')
            if 'timeseries' in kwargs:
                self.ts = Timeseries(self.client, type='gauge', intervals=kwargs['timeseries'])
            else:
                self.ts = Timeseries(self.client, type='gauge', intervals={
                    'seconds': {
                        'step': 5,  # 5 seconds
                        'steps': 120,  # last 10 minutes
                        'read_cast': float,
                    }
                }
                                     )

    def record_hit(self, key, measurement):
        if self.client:
            self.ts.insert(str(key), float(measurement))

    def record_response_time(self, content_id, measurement):
        self.record_hit(str(content_id) + ':rt', float(measurement))

    def record_status(self, content_id, measurement):
        self.record_hit(str(content_id) + ':status', float(measurement))

    def get_timeseries(self, key):
        if self.client is not None:
            # logging.info("properties: %s", str(self.ts.properties(str(content_id) + ':' + str(monitor_id))) )
            return self.ts.series(str(key), 'seconds')

        return None

    def get_response_time_timeseries(self, content_id):
        # logging.info("properties: %s", str(self.ts.properties(str(content_id) + ':' + str(monitor_id))) )
        return self.get_timeseries(str(content_id) + ':rt')

    def get_status_timeseries(self, content_id):
        # logging.info("properties: %s", str(self.ts.properties(str(content_id) + ':' + str(monitor_id))) )
        return self.get_timeseries(str(content_id) + ':status')

    def get_timeseries_avg(self, key):
        # logging.info("properties: %s", str(self.ts.properties(str(content_id) + ':' + str(monitor_id))) )
        series = []
        avg = 0.0
        if self.client is not None:
            series = self.ts.series(str(key), 'seconds')

            summ = 0.0
            count = 0.0
            for key, value in series.items():
                if value:
                    summ += float(value)
                    count += 1.0

            if count > 0.0:
                avg = summ / count

        logging.debug('serie avg: %f', avg)
        return avg

    def get_response_time_avg(self, content_id):
        return self.get_timeseries_avg(str(content_id) + ':rt')
