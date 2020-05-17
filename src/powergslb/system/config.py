import ast
import ConfigParser

import logging
import json

__all__ = ['SmartConfigParser', 'get_config', 'parse_config']

__config = None


class SmartConfigParser(ConfigParser.RawConfigParser, object):
    """
    Smart parse config file values
    """

    def __init__(self, files, **kwargs):
        super(SmartConfigParser, self).__init__(**kwargs)
        self.read(files)

    def get(self, section, option):
        value = super(SmartConfigParser, self).get(section, option)
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            pass

        return value

    def items(self, section):
        smart_items = []
        for key, value in super(SmartConfigParser, self).items(section):
            logging.debug('input - key: %s- value: %s -', str(key), str(value))

            # Try Litteral
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                # Try JSON
                try:
                    value = json.loads(value)
                except:
                    pass

            logging.debug('output - key: %s- value: %s', str(key), str(value))
            smart_items.append((key, value))

        return dict(smart_items)


def get_config():
    return __config


def parse_config(files):
    global __config
    __config = SmartConfigParser(files)
