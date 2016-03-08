import logging

import mysql.connector

from powergslb.database.mysql.powerdns import PowerDNSDatabaseMixIn
from powergslb.database.mysql.w2ui import W2UIDatabaseMixIn

__all__ = ['MySQLDatabase']


class MySQLDatabase(PowerDNSDatabaseMixIn, W2UIDatabaseMixIn, mysql.connector.MySQLConnection):
    """
    MySQLDatabase class
    """
    Error = mysql.connector.Error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.disconnect()

    @staticmethod
    def join_operation(operation):
        return ' '.join(filter(bool, (line.strip() for line in operation.splitlines())))

    def _execute(self, operation, params=()):
        operation = self.join_operation(operation)
        if params:
            logging.debug('{}: "{}" % {}'.format(type(self).__name__, operation, params))
        else:
            logging.debug('{}: "{}"'.format(type(self).__name__, operation))

        cursor = self.cursor(buffered=True)
        try:
            cursor.execute(operation, params)
            if operation.startswith('SELECT'):
                logging.debug('{}: {} rows returned'.format(type(self).__name__, cursor.rowcount))
                column_names = [description[0] for description in cursor.description]
                result = [dict(zip(column_names, row)) for row in cursor]
            else:
                logging.debug('{}: {} rows affected'.format(type(self).__name__, cursor.rowcount))
                result = cursor.rowcount
        finally:
            cursor.close()

        return result
