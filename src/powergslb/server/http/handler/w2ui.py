import json
import logging
import operator

from powergslb.server.http.handler.abstract import AbstractContentHandler
from powergslb.server.http.handler.queryparser import parse_query

import powergslb.database
import powergslb.monitor

__all__ = ['W2UIContentHandler']


class W2UIContentHandler(AbstractContentHandler):
    """
    w2ui content handler
    """
    _commands = {
        'delete-records': '_delete_records',
        'get-items': '_get_items',
        'get-record': '_get_record',
        'get-records': '_get_records',
        'save-record': '_save_record'
    }

    _search_functions = {
        'int': {
            'is': lambda x, y: int(x) == int(y),
            'in': lambda x, y: (isinstance(y, list) and int(x) in y) or (int(x) in [int(y)]),
            'not in': lambda x, y: (isinstance(y, list) and int(x) not in y) or (int(x) not in [int(y)]),
            'between': lambda x, y: int(y[0]) <= int(x) <= int(y[1])
        },
        'text': {
            'is': lambda x, y: str(x).lower() == str(y).lower(),
            'begins': lambda x, y: str(x).lower().startswith(str(y).lower()),
            'contains': lambda x, y: str(y).lower() in str(x).lower(),
            'ends': lambda x, y: str(x).lower().endswith(str(y).lower())
        }
    }

    def _delete_records(self):
        data = self.query.get('data')
        selected = self.query.get('selected')
        if not isinstance(selected, list):
            selected = [selected]

        if not hasattr(self.database, 'delete_' + data):
            content = {'status': 'error', 'message': "delete-records '{}' not implemented".format(data)}
        else:
            count = getattr(self.database, 'delete_' + data)(selected)
            if not count:
                content = {'status': 'error', 'message': 'records not deleted'}
            else:
                content = {'status': 'success'}

        return content

    def _get_items(self):
        data = self.query.get('data')
        field = self.query.get('field')

        if not hasattr(self.database, 'get_' + data):
            content = {'status': 'error', 'message': "get-items '{}' not implemented".format(data)}
        else:
            records = getattr(self.database, 'get_' + data)()
            items = [record.get(field) for record in self._limit_records(records) if record.get(field) is not None]
            content = {'status': 'success', 'items': items}

        return content

    def _get_record(self):
        data = self.query.get('data')
        recid = int(self.query.get('recid'))

        if not hasattr(self.database, 'get_' + data):
            content = {'status': 'error', 'message': "get-record '{}' not implemented".format(data)}
        else:
            records = getattr(self.database, 'get_' + data)(recid)
            content = {'status': 'success', 'record': records[0]}

        return content

    def _get_records(self):
        data = self.query.get('data')

        if not hasattr(self.database, 'get_' + data):
            content = {'status': 'error', 'message': "get-records '{}' not implemented".format(data)}
        else:
            records = getattr(self.database, 'get_' + data)()
            if data == 'status':
                self._update_status(records)
            records = self._search_records(records)
            self._sort_records(records)
            content = {'status': 'success', 'total': len(records), 'records': self._limit_records(records)}

        return content

    def _limit_records(self, records):
        if 'limit' in self.query and 'offset' in self.query:
            limit = int(self.query['limit'])
            offset = int(self.query['offset'])
            records = records[offset:offset + limit]
        elif 'max' in self.query:
            limit = int(self.query['max'])
            records = records[:limit]

        return records

    def _parse_query(self):
        if self.query:
            self.query = parse_query(self.query)

        elif self.body:
            self.query = parse_query(self.body)

        logging.debug('{}: query: {}'.format(type(self).__name__, self.query))

    def _save_record(self):
        data = self.query.get('data')
        recid = int(self.query.get('recid'))
        record = self.query.get('record')

        if not hasattr(self.database, 'save_' + data):
            content = {'status': 'error', 'message': "save-record '{}' not implemented".format(data)}
        else:
            count = getattr(self.database, 'save_' + data)(recid, **record)
            if not count:
                content = {'status': 'error', 'message': 'record not changed'}
            else:
                content = {'status': 'success'}

        return content

    def _search_records(self, records):
        if 'search' not in self.query:
            return records

        all_indexes = set(range(len(records)))
        final_indexes = set()

        for search in self.query['search']:
            search_function = self._search_functions.get(search['type']).get(search['operator'])
            if not callable(search_function):
                continue

            search_indexes = set()
            for i in all_indexes:
                if search_function(records[i][search['field']], search['value']):
                    search_indexes.add(i)

            if self.query['searchLogic'] == 'AND':
                all_indexes = search_indexes

            elif self.query['searchLogic'] == 'OR':
                final_indexes.update(search_indexes)

        if self.query['searchLogic'] == 'AND':
            final_indexes = all_indexes

        return [records[i] for i in final_indexes]

    def _sort_records(self, records):
        if 'sort' in self.query:
            for sort in self.query['sort']:
                reverse = sort['direction'] == 'desc'
                records.sort(key=operator.itemgetter(sort['field']), reverse=reverse)

    @staticmethod
    def _update_status(records):
        for record in records:
            if record['disabled'] or record['id'] in powergslb.monitor.get_status():
                record['status'] = 'Off'
                record['style'] = 'color: red'
            else:
                record['status'] = 'On'
                record['style'] = 'color: green'

            del record['id']

    def content(self):
        self._parse_query()
        command = self.query.get('cmd')

        if self._commands.get(command) is None:
            content = {'status': 'error', 'message': "command '{}' not implemented".format(command)}
        else:
            try:
                content = getattr(self, self._commands.get(command))()
            except powergslb.database.Database.Error as e:
                logging.error('{}: {}: {}'.format(type(self).__name__, type(e).__name__, e))
                content = {'status': 'error', 'message': str(e)}

        return json.dumps(content, separators=(',', ':'))
