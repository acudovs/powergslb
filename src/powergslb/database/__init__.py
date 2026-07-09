"""Database access; exposes the MySQL/MariaDB implementation as Database."""

from powergslb.database.mysql import MySQLDatabase as Database
from powergslb.database.page import PageRequest

__all__ = ['Database', 'PageRequest']
