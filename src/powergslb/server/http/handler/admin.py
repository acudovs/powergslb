"""Admin interface handler: Basic Auth, w2ui CRUD protocol, and static assets."""

import base64
import datetime
import email.utils
import gzip
import io
import json
import logging
import os
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from typing import Any, Callable, ClassVar, BinaryIO

import brotli

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

    _cache_control: ClassVar[str | None] = 'no-store'

    _commands: ClassVar[dict[str, str]] = {
        'delete-records': '_delete_records',
        'get-items': '_get_items',
        'get-record': '_get_record',
        'get-records': '_get_records',
        'save-record': '_save_record'
    }

    # (Content-Encoding token, precompressed sibling suffix, per-request compressor), most preferred first.
    _encodings: ClassVar[tuple[tuple[str, str, Callable[[bytes], bytes]], ...]] = (
        ('br', '.br', lambda data: brotli.compress(data, quality=5)),
        ('gzip', '.gz', lambda data: gzip.compress(data, compresslevel=6)),
    )
    # Below this size a dynamic response is sent uncompressed.
    _min_encode_size: ClassVar[int] = 256

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

    def send_head(self) -> io.BytesIO | BinaryIO | None:
        """Serve a static asset, preferring a precompressed sibling the client accepts.

        A resolved file or a directory index is served from here; everything else stays pure stdlib.

        :returns: An open file object for the caller to stream and close, or None when nothing further remains.
        """
        path = self._static_file_path()
        if path is None:
            return super().send_head()

        accepted = self._accepted_encodings()
        for encoding, suffix, _ in self._encodings:
            encoded_path = path + suffix
            if encoding in accepted and os.path.isfile(encoded_path):
                return self._send_static_head(path, encoded_path, encoding)

        return self._send_static_head(path, path, None)

    def _static_file_path(self) -> str | None:
        """Resolve the request to the on-disk regular file stdlib would serve.

        A directory URL resolves to its index page; a directory without a trailing slash or index page returns None.

        :returns: The file path to negotiate, or None.
        """
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            if not urllib.parse.urlsplit(self.path).path.endswith('/'):
                return None

            for index in self.index_pages:
                index_path = os.path.join(path, index)
                if os.path.isfile(index_path):
                    return index_path
            return None

        return path if os.path.isfile(path) else None

    def _accepted_encodings(self) -> set[str]:
        """Parse Accept-Encoding into the set of codings the client accepts (dropping any q=0 token).

        :returns: The lowercased coding tokens with a non-zero q-value.
        """
        accepted = set()
        for part in self.headers.get('Accept-Encoding', '').split(','):
            tokens = part.split(';')
            coding = tokens[0].strip().lower()
            if not coding:
                continue
            quality = 1.0
            for param in tokens[1:]:
                param = param.strip()
                if param.startswith('q='):
                    try:
                        quality = float(param[2:])
                    except ValueError:
                        quality = 0.0  # unparseable q counts as a refusal (nginx-style)
            if quality > 0:
                accepted.add(coding)
        return accepted

    def _encode_body(self, content_bytes: bytes) -> tuple[bytes, str | None]:
        """Compress a dynamic response, negotiating brotli then gzip against Accept-Encoding.

        Bodies under _min_encode_size go out identity (the CPU is not worth the tiny reply).

        :param content_bytes: The identity response body.
        :returns: The (possibly compressed) body and its Content-Encoding token, or None for identity.
        """
        if len(content_bytes) < self._min_encode_size:
            return content_bytes, None

        accepted = self._accepted_encodings()
        for encoding, _, compress in self._encodings:
            if encoding in accepted:
                return compress(content_bytes), encoding

        return content_bytes, None

    def _send_static_head(self, path: str, disk_path: str, encoding: str | None) -> io.BytesIO | BinaryIO | None:
        """Send headers for a static asset, mirroring the stdlib static path.

        The Content-Type is guessed from the original file, not the .br/.gz twin; Content-Length, Last-Modified and
        the If-Modified-Since comparison all use the file actually served; Vary: Accept-Encoding rides every
        representation so a shared cache keys the identity and precompressed bodies apart.

        :param path: The original file path, used only to guess the Content-Type.
        :param disk_path: The identity file or a precompressed sibling to open and serve.
        :param encoding: The Content-Encoding token for a sibling, or None to serve the identity file.
        :returns: The open file object, or None on a 304.
        """
        ctype = self.guess_type(path)
        try:
            f = open(disk_path, 'rb')  # pylint: disable=consider-using-with
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        try:
            fs = os.fstat(f.fileno())
            if 'If-Modified-Since' in self.headers and 'If-None-Match' not in self.headers:
                try:
                    ims = email.utils.parsedate_to_datetime(self.headers['If-Modified-Since'])
                except (TypeError, IndexError, OverflowError, ValueError):
                    pass
                else:
                    if ims.tzinfo is None:
                        ims = ims.replace(tzinfo=datetime.timezone.utc)
                    if ims.tzinfo is datetime.timezone.utc:
                        last_modif = datetime.datetime.fromtimestamp(fs.st_mtime, datetime.timezone.utc)
                        last_modif = last_modif.replace(microsecond=0)
                        if last_modif <= ims:
                            self.send_response(HTTPStatus.NOT_MODIFIED)
                            self.end_headers()
                            f.close()
                            return None

            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', ctype)
            if encoding is not None:
                self.send_header('Content-Encoding', encoding)
            self.send_header('Content-Length', str(fs[6]))
            self.send_header('Last-Modified', self.date_time_string(fs.st_mtime))
            self.send_header('Vary', 'Accept-Encoding')
            self.end_headers()
            return f

        except BaseException:
            f.close()
            raise

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

        logging.debug('query: %s', self._masked_query())

    def _masked_query(self) -> Any:
        """Return the query with any posted record password masked for safe logging.

        :returns: A shallow copy with record password masked, or self.query unchanged when there is none.
        """
        record = self.query.get('record') if isinstance(self.query, dict) else None
        if isinstance(record, dict) and 'password' in record:
            return {**self.query, 'record': {**record, 'password': self._mask}}
        return self.query

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
