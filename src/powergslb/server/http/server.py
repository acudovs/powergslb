import BaseHTTPServer
import logging
import os
import SocketServer

from powergslb.server.http.handler import HTTPRequestHandler

import powergslb.system

__all__ = ['HTTPServerThread', 'ThreadingHTTPServer']


class HTTPServerThread(powergslb.system.AbstractThread):
    """
    PowerGSLB server thread
    """

    def __init__(self, **kwargs):
        super(HTTPServerThread, self).__init__(**kwargs)
        self.address = powergslb.system.get_config().get('server', 'address')
        self.port = powergslb.system.get_config().get('server', 'port')
        self.root = powergslb.system.get_config().get('server', 'root')
        os.chdir(self.root)

    def task(self):
        http_server = ThreadingHTTPServer((self.address, self.port), HTTPRequestHandler)
        http_server.daemon_threads = True
        logging.info('{}: listening on {}:{}'.format(type(self).__name__, self.address, self.port))
        http_server.serve_forever()


class ThreadingHTTPServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    """
    Threading HTTP Server handles each request in a new thread
    """
    pass
