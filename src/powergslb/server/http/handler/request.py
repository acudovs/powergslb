import base64
import logging
import os
import SimpleHTTPServer
import urllib2

import netaddr

from powergslb.server.http.handler.powerdns import PowerDNSContentHandler
from powergslb.server.http.handler.w2ui import W2UIContentHandler

import powergslb
import powergslb.database
import powergslb.system

__all__ = ['HTTPRequestHandler']


class HTTPRequestHandler(SimpleHTTPServer.SimpleHTTPRequestHandler, object):
    """
    HTTP request handler
    """
    protocol_version = 'HTTP/1.1'
    server_version = 'PowerGSLB/' + powergslb.__version__
    rbufsize = -1
    wbufsize = -1

    def __init__(self, *args):
        self._authorization_header = None
        self.body = None
        self.close_connection = 0
        self.database = None
        self.dirs = None
        self.path = None
        self.remote_ip = None
        self.query = None
        super(HTTPRequestHandler, self).__init__(*args)

    def _is_authorized(self):
        authorized = False
        authorization_header = self.headers.get('Authorization')

        if self._authorization_header:
            authorized = self._authorization_header == authorization_header

        elif authorization_header:
            try:
                scheme, base64_user_password = authorization_header.split(' ')
                user, password = base64.b64decode(base64_user_password).split(':')
            except Exception as e:
                logging.error('{}: authorization error: {}'.format(type(self).__name__, e))
            else:
                authorized = scheme == 'Basic' and self.database.check_user(user, password)
                if authorized:
                    logging.debug("{}: user '{}' authorized".format(type(self).__name__, user))
                    self._authorization_header = authorization_header
                else:
                    logging.error("{}: user '{}' not authorized".format(type(self).__name__, user))

        return authorized

    def _handle_request(self):
        self._set_remote_ip()
        self._urlsplit()

        if not self.dirs:
            self.send_error(501)

        elif self.command == 'GET' and self.dirs[0] == 'dns':
            content_handler = PowerDNSContentHandler(self)
            self._send_content(content_handler.content())

        elif self.command in ['GET', 'POST'] and self.dirs[0] == 'admin':
            if not self._is_authorized():
                self._send_authenticate()

            elif len(self.dirs) == 2 and self.dirs[1] == 'w2ui':
                content_handler = W2UIContentHandler(self)
                self._send_content(content_handler.content(), debug=self.command == 'GET')
            else:
                local_path = self.translate_path(self.path)
                if os.path.isdir(local_path) and not self.path.endswith('/'):
                    self._send_redirect(self.path + '/')
                else:
                    super(HTTPRequestHandler, self).do_GET()
        else:
            self.send_error(404)

    def _read_body(self):
        try:
            content_length = int(self.headers.get('Content-Length'), 0)
        except ValueError:
            raise Exception("'Content-Length' header invalid: '{}'".format(
                self.headers.get('Content-Length')))
        else:
            self.body = self.rfile.read(content_length)

    def _send_authenticate(self, code=401):
        message, explain = self.responses[code]
        content = self.error_message_format % {'code': code, 'message': message, 'explain': explain}
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.send_header('WWW-Authenticate', 'Basic realm="{}"'.format(self.server_version))
        self.end_headers()
        self.wfile.write(content)

    def _send_content(self, content, code=200, debug=True):
        self.send_response(code)
        self.send_header('Content-Type', 'text/javascript; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)
        if debug:
            logging.debug('{}: {}'.format(type(self).__name__, content))

    def _send_redirect(self, location, code=301):
        self.send_response(code)
        self.send_header('Location', location)
        self.send_header('Connection', 'close')
        self.end_headers()
        logging.debug('{}: {}'.format(type(self).__name__, location))

    def _set_remote_ip(self):
        remote_ip = self.client_address[0]
        if 'X-Remotebackend-Real-Remote' in self.headers:
            try:
                real_remote_header = self.headers.get('X-Remotebackend-Real-Remote')
                remote_ip = netaddr.IPNetwork(real_remote_header).ip.format()
            except (netaddr.AddrFormatError, ValueError) as e:
                logging.error("{}: 'X-Remotebackend-Real-Remote' header invalid: {}: {}".format(
                    type(self).__name__, type(e).__name__, e))

        self.remote_ip = remote_ip

    def _urlsplit(self):
        self.path, self.query = urllib2.httplib.urlsplit(self.path)[2:4]
        self.dirs = self.path.split('/')[1:]

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._read_body()
        self._handle_request()

    def handle(self):
        try:
            with powergslb.database.Database(**powergslb.system.get_config().items('database')) as self.database:
                while not self.close_connection:
                    self.handle_one_request()
        except powergslb.database.Database.Error as e:
            logging.error('{}: {}: {}'.format(type(self).__name__, type(e).__name__, e))
