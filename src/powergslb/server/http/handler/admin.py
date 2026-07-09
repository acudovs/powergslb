"""Admin interface handler: Basic Auth, w2ui CRUD protocol, and static assets."""

import base64
import json
import logging
from http.server import SimpleHTTPRequestHandler
from typing import Any, ClassVar

from powergslb.database import PageRequest
from powergslb.monitor import MonitorManager
from powergslb.routing import RoutingPolicy
from powergslb.server.http.handler.queryparser import QueryParserError, parse_query
from powergslb.server.http.handler.request import HTTPRequestHandler
from powergslb.view import ViewRule

__all__ = ['AdminRequestHandler']


class AdminRequestHandler(HTTPRequestHandler):
    """Serves the admin interface: Basic Auth, w2ui grid CRUD at /admin/w2ui, and static assets.

    Passes the w2ui query to the database get_data/save_data/delete_data dispatchers. Search, sort, and paging run in
    SQL: the handler translates the query into a PageRequest and the database composes it into the SQL read.
    """
    route: ClassVar[str] = 'admin'

    _commands: ClassVar[dict[str, str]] = {
        'delete-records': '_delete_records',
        'get-items': '_get_items',
        'get-record': '_get_record',
        'get-records': '_get_records',
        'save-record': '_save_record'
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
        """Validate Basic Auth credentials against the database; any parse failure counts as unauthorized.

        :returns: True when the request carries valid credentials.
        """
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
        """Send the Basic Auth challenge with an HTML error body; a HEAD challenge carries no body.

        :param code: HTTP status code of the challenge.
        """
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

    def _delete_records(self) -> dict[str, Any]:
        """Handle the delete-records command: delete the selected rows from the database.

        :returns: The w2ui status reply.
        """
        data = self.query.get('data')
        selected = self.query.get('selected')
        if not isinstance(selected, list):
            selected = [selected]

        if not self.database.delete_data(data, selected):
            return {'status': 'error', 'message': 'records not deleted'}
        return {'status': 'success'}

    def _get_data(self, data: Any, recid: int = 0,
                  page: PageRequest | None = None) -> tuple[list[dict[str, Any]], int]:
        """Read a token's table from the database; the status table needs the down-id snapshot.

        :param data: The table token from the query.
        :param recid: The key value to fetch; 0 fetches every row.
        :param page: The search/sort/paging request; None returns every matching row.
        :returns: The matching rows and the total match count.
        """
        if data == 'status':
            return self.database.get_data(data, recid, page, down_ids=self.status_registry.snapshot())
        return self.database.get_data(data, recid, page)

    def _get_items(self) -> dict[str, Any]:
        """Handle the get-items command: collect one field's values from the database for a combo dropdown.

        :returns: The w2ui reply with the collected items.
        """
        data = self.query.get('data')
        field = self.query.get('field')
        records, _ = self._get_data(data, page=PageRequest.from_query(self.query))
        items = [record.get(field) for record in records if record.get(field) is not None]
        return {'status': 'success', 'items': items}

    def _get_record(self) -> dict[str, Any]:
        """Handle the get-record command: fetch one row from the database by recid.

        :returns: The w2ui reply with the record, or an error when the recid does not exist.
        """
        data = self.query.get('data')
        recid = int(self.query.get('recid'))

        records, _ = self._get_data(data, recid)
        if not records:
            return {'status': 'error', 'message': f"get-record '{data}' id {recid} not found"}
        return {'status': 'success', 'record': records[0]}

    def _get_records(self) -> dict[str, Any]:
        """Handle the get-records command: read the database records searched, sorted and paged in SQL.

        :returns: The w2ui reply with the total match count and the requested page.
        """
        data = self.query.get('data')
        records, total = self._get_data(data, page=PageRequest.from_query(self.query))
        if data == 'status':
            self._style_status(records)
        return {'status': 'success', 'total': total, 'records': records}

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
        """Handle the save-record command: validate the posted record, then insert or update it.

        :returns: The w2ui status reply.
        """
        data = self.query.get('data')
        recid = int(self.query.get('recid'))
        record = self.query.get('record')

        self._validate_record(data, record)

        if not self.database.save_data(data, recid, **record):
            return {'status': 'error', 'message': 'record not changed'}
        return {'status': 'success'}

    @staticmethod
    def _validate_record(data: Any, record: dict[str, Any]) -> None:
        """Reject an invalid record before the database write.

        :param data: The table token from the query.
        :param record: The record fields posted by the admin form.
        :raises ValueError: When validation failed.
        """
        if data == 'monitors':
            # The record content is unknown at monitor-definition time; validate a placeholder IP.
            MonitorManager.build_check({'content': '127.0.0.1', 'monitor_json': record['monitor_json']})

        elif data == 'routings':
            RoutingPolicy.resolve(record['policy_json'])

        elif data == 'views':
            ViewRule.resolve(record['rule'])

    @staticmethod
    def _style_status(records: list[dict[str, Any]]) -> None:
        """Annotate each status row with its display style from the SQL-computed status value.

        :param records: The status rows to annotate in place.
        """
        for record in records:
            record['style'] = 'color: red' if record['status'] == 'Off' else 'color: green'

    def content(self) -> str:
        """Dispatch the w2ui cmd to its handler; an unknown command or a handler error becomes an error reply.

        A deliberate validation error (ValueError) is surfaced to the UI; any other exception is logged and
        answered with a generic message, so an internal failure (database, type) is not disclosed to the client.

        :returns: The JSON-encoded w2ui reply.
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
