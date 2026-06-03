"""Command line entry point."""

import argparse
import logging

from powergslb.monitor import MonitorManager, StatusRegistry
from powergslb.server import AdminRequestHandler, PowerDNSRequestHandler, ServerManager
from powergslb.system import Config, ServiceThread, SystemService

__all__ = ['PowerGSLB']


class PowerGSLB:
    """Main program: parses arguments and wires the service threads."""

    @staticmethod
    def main() -> None:
        """Parse arguments, load the config, and run the service threads under SystemService."""
        args_parser = argparse.ArgumentParser()
        args_parser.add_argument('-c', '--config', required=True)
        args = args_parser.parse_args()

        config = Config(args.config)

        logging.basicConfig(
            format=config.get('logging', 'format'),
            level=config.get('logging', 'level')
        )

        database = config.items('database')
        status = StatusRegistry()

        service_threads: list[ServiceThread] = [
            MonitorManager(config.items('monitor'), database, status, name='Monitor'),
            ServerManager(config.items('admin'), database, status, AdminRequestHandler, name='Admin'),
            ServerManager(config.items('server'), database, status, PowerDNSRequestHandler, name='Server')
        ]

        service = SystemService(service_threads)
        service.start()
