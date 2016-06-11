import argparse
import logging

import powergslb.monitor
import powergslb.server
import powergslb.system

__all__ = ['PowerGSLB']


class PowerGSLB(object):
    """
    PowerGSLB main program
    """

    @staticmethod
    def main():
        args_parser = argparse.ArgumentParser()
        args_parser.add_argument('-c', '--config')
        args = args_parser.parse_args()

        powergslb.system.parse_config(args.config)
        config = powergslb.system.get_config()

        logging.basicConfig(
                format=config.get('logging', 'format'),
                level=logging.getLevelName(config.get('logging', 'level'))
        )

        service_threads = [
            powergslb.monitor.MonitorThread(name='Monitor'),
            powergslb.server.ServerThread(name='Server')
        ]

        service = powergslb.system.SystemService(service_threads)
        service.start()
