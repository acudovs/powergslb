"""Admin interface handler: Basic Auth, w2ui CRUD protocol, and static assets."""

import base64
import json
import logging
import operator
from http.server import SimpleHTTPRequestHandler
from typing import Any, Callable, ClassVar

import netaddr

from powergslb.monitor import MonitorManager
from powergslb.server.http.handler.queryparser import QueryParserError, parse_query
from powergslb.server.http.handler.request import HTTPRequestHandler

__all__ = ['AdminRequestHandler']


class AdminRequestHandler(HTTPRequestHandler):
    """Serves the admin interface: Basic Auth, w2ui grid CRUD at /admin/w2ui, and static assets.

    Search, sort, and paging are applied in Python over the full result set, matching the w2ui protocol.
    """
    route: ClassVar[str] = 'admin'

    _commands: ClassVar[dict[str, str]] = {
        'delete-records': '_delete_records',
        'get-items': '_get_items',
        'get-record': '_get_record',
        'get-records': '_get_records',
        'save-record': '_save_record'
    }

    _data_tables: ClassVar[set[str]] = {'domains', 'monitors', 'records', 'status', 'types', 'users', 'views'}

    _search_functions: ClassVar[dict[str, dict[str, Callable[[Any, Any], bool]]]] = {
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

    def _handle_route(self) -> None:
        """Authenticate, then serve the w2ui CRUD endpoint or fall through to the static admin assets."""
        if not self._is_authorized():
            self._send_authenticate()
        elif len(self.dirs) == 2 and self.dirs[1] == 'w2ui':
            if self.command in ('GET', 'POST'):
                self._send_content(self.content(), debug=self.command == 'GET')
            else:
                self.send_error(404)
        elif self.command == 'GET':
            SimpleHTTPRequestHandler.do_GET(self)
        elif self.command == 'HEAD':
            SimpleHTTPRequestHandler.do_HEAD(self)
        else:
            self.send_error(404)

    def _is_authorized(self) -> bool:
        """Validate Basic Auth credentials against the database; any parse failure counts as unauthorized."""
        authorized = False
        authorization_header = self.headers.get('Authorization')

        if authorization_header:
            try:
                scheme, base64_user_password = authorization_header.split(' ', 1)
                user, password = base64.b64decode(base64_user_password).decode('utf-8').split(':', 1)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.error('authorization error: %s', e)
            else:
                authorized = scheme.lower() == 'basic' and bool(self.database.check_user(user, password))
                if authorized:
                    logging.debug("user '%s' authorized", user)
                else:
                    logging.error("user '%s' not authorized", user)

        return authorized

    def _send_authenticate(self, code: int = 401) -> None:
        message, explain = self.responses[code]
        content = self.error_message_format % {'code': code, 'message': message, 'explain': explain}
        content_bytes = content.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content_bytes)))
        self.send_header('WWW-Authenticate', f'Basic realm="{self.server_version}"')
        self.end_headers()
        if self.command != 'HEAD':  # a HEAD challenge carries the same headers but no body
            self.wfile.write(content_bytes)

    def _database_method(self, prefix: str, data: Any) -> 'Callable[..., Any]':
        """Resolve the database CRUD method for a whitelisted table token.

        :raises ValueError: When data is not a whitelisted table; content() turns it into an error reply.
        """
        method = getattr(self.database, prefix + data, None) if data in self._data_tables else None
        if method is None:
            raise ValueError(f"'{data}' not implemented")
        return method

    def _delete_records(self) -> dict[str, Any]:
        data = self.query.get('data')
        selected = self.query.get('selected')
        if not isinstance(selected, list):
            selected = [selected]

        if not self._database_method('delete_', data)(selected):
            return {'status': 'error', 'message': 'records not deleted'}
        return {'status': 'success'}

    def _get_items(self) -> dict[str, Any]:
        data = self.query.get('data')
        field = self.query.get('field')

        records = self._database_method('get_', data)()
        items = [record.get(field) for record in self._limit_records(records) if record.get(field) is not None]
        return {'status': 'success', 'items': items}

    def _get_record(self) -> dict[str, Any]:
        data = self.query.get('data')
        recid = int(self.query.get('recid'))

        records = self._database_method('get_', data)(recid)
        if not records:
            return {'status': 'error', 'message': f"get-record '{data}' id {recid} not found"}
        return {'status': 'success', 'record': records[0]}

    def _get_records(self) -> dict[str, Any]:
        data = self.query.get('data')

        records = self._database_method('get_', data)()
        if data == 'status':
            self._update_status(records)
        records = self._search_records(records)
        self._sort_records(records)
        return {'status': 'success', 'total': len(records), 'records': self._limit_records(records)}

    def _limit_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply limit/offset or max paging from the query."""
        if 'limit' in self.query and 'offset' in self.query:
            limit = int(self.query['limit'])
            offset = int(self.query['offset'])
            records = records[offset:offset + limit]
        elif 'max' in self.query:
            limit = int(self.query['max'])
            records = records[:limit]

        return records

    def _parse_query(self) -> None:
        """Parse the query string (GET) or request body (POST) into self.query; a parse error yields an empty query."""
        try:
            if self.query:
                self.query = parse_query(self.query)
            elif self.body:
                self.query = parse_query(self.body.decode('utf-8'))
            else:
                self.query = {}
        except QueryParserError as e:
            logging.error('query parse error: %s', e)
            self.query = {}

        logging.debug('query: %s', self.query)

    def _save_record(self) -> dict[str, Any]:
        data = self.query.get('data')
        recid = int(self.query.get('recid'))
        record = self.query.get('record')

        method = self._database_method('save_', data)
        self._validate_record(data, record)
        if not method(recid, **record):
            return {'status': 'error', 'message': 'record not changed'}
        return {'status': 'success'}

    def _validate_record(self, data: Any, record: dict[str, Any]) -> None:
        """Reject an invalid record before the database write.

        :raises ValueError: When validation failed.
        """
        if data == 'monitors':
            # The record content is unknown at monitor-definition time; validate a placeholder IP.
            MonitorManager.build_check({'content': '127.0.0.1', 'monitor_json': record['monitor_json']})

        elif data == 'views':
            # The rule is a space-separated list or CIDR and geo tokens; reject anything unparsable.
            tokens = record['rule'].split()
            if not tokens:
                raise ValueError('view rule must hold at least one token')

            for token in tokens:
                if self.geoip_reader.parse_geo_token(token) is not None:
                    continue
                try:
                    netaddr.IPNetwork(token)
                except netaddr.AddrFormatError as e:
                    raise ValueError(f'view rule CIDR invalid: {token}') from e

    def _search_match(self, record: dict[str, Any], search: dict[str, Any]) -> 'bool | None':
        """Apply one search to one record.

        :returns: None when the search type/operator is unknown (skip it); otherwise the match result,
            with a missing field or unconvertible value counting as no match.
        """
        search_function = (self._search_functions.get(search['type']) or {}).get(search['operator'])
        if not callable(search_function):
            return None
        try:
            return bool(search_function(record[search['field']], search['value']))
        except (KeyError, ValueError, IndexError, TypeError):
            return False

    def _search_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter records by the query searches, combined with AND unless searchLogic is OR."""
        searches = self.query.get('search')
        if not searches:
            return records

        or_logic = self.query.get('searchLogic') == 'OR'  # any non-'OR' (including absent) is AND
        matched = []
        for record in records:
            results = [match for match in (self._search_match(record, search) for search in searches)
                       if match is not None]  # drop searches with an unknown type/operator
            keep = (any(results) if or_logic else all(results)) if results else not or_logic
            if keep:
                matched.append(record)
        return matched

    def _sort_records(self, records: list[dict[str, Any]]) -> None:
        """Sort records in place by the query's sort keys; unknown fields are ignored."""
        if 'sort' in self.query:
            # w2ui sends sort keys primary-first; apply them least-significant-first so the primary key dominant.
            for sort in reversed(self.query['sort']):
                if records and sort['field'] not in records[0]:
                    continue
                reverse = sort['direction'] == 'desc'
                records.sort(key=operator.itemgetter(sort['field']), reverse=reverse)

    def _update_status(self, records: list[dict[str, Any]]) -> None:
        """Annotate each record with its On/Off status and display style; drop the internal id."""
        for record in records:
            if record['disabled'] or self.status_registry.is_down(record['id']):
                record['status'] = 'Off'
                record['style'] = 'color: red'
            else:
                record['status'] = 'On'
                record['style'] = 'color: green'

            del record['id']

    def content(self) -> str:
        """Dispatch the w2ui cmd to its handler; an unknown command or a handler error becomes an error reply.

        A deliberate validation error (ValueError) is surfaced to the UI; any other exception is logged and
        answered with a generic message, so an internal failure (database, type) is not disclosed to the client.
        """
        self._parse_query()
        command = self.query.get('cmd')
        method_name = self._commands.get(command) if isinstance(command, str) else None

        if method_name is None:
            content: dict[str, Any] = {'status': 'error', 'message': f"command '{command}' not implemented"}
        else:
            try:
                content = getattr(self, method_name)()
            except ValueError as e:
                logging.error('%s: %s', type(e).__name__, e)
                content = {'status': 'error', 'message': str(e)}
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.error('%s: %s', type(e).__name__, e)
                content = {'status': 'error', 'message': 'internal error'}

        return json.dumps(content, separators=(',', ':'))
