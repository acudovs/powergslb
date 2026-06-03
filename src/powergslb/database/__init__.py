"""Database access; exposes the MySQL/MariaDB implementation as Database."""

from powergslb.database.mysql import MySQLDatabase as Database

__all__ = ['Database']
