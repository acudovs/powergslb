import BaseHTTPServer
import logging
import os
import SocketServer
import ssl

from powergslb.server.http.handler import HTTPRequestHandler

import powergslb.system

__all__ = ['HTTPServerThread', 'ThreadingHTTPServer']


class HTTPServerThread(powergslb.system.AbstractThread):
    """
    PowerGSLB server thread
    """

    def __init__(self, server_config, **kwargs):
        super(HTTPServerThread, self).__init__(**kwargs)
        self.address = server_config.get('address')
        self.port = server_config.get('port')
        self.ssl = server_config.get('ssl')
        self.key = server_config.get('key')
        self.cert = server_config.get('cert')
        self.ciphers = server_config.get('ciphers')
        self.root = server_config.get('root')
        os.chdir(self.root)

    def task(self):
        address = (self.address, self.port)
        http_server = ThreadingHTTPServer(address, HTTPRequestHandler)
        http_server.daemon_threads = True
        if self.ssl:
            http_server.socket = ssl.wrap_socket(
                http_server.socket, keyfile=self.key, certfile=self.cert, server_side=True, ciphers=self.ciphers)
            http_server.socket.context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
        logging.info('{}: listening on {}:{}'.format(type(self).__name__, self.address, self.port))
        http_server.serve_forever()


class ThreadingHTTPServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    """
    Threading HTTP Server handles each request in a new thread
    """
    pass
