"""GeoIP lookup and geo-token grammar.

Parses geo selectors (country:<ISO>, continent:<CODE>) and resolves a client IP to its country and continent.
"""

import logging
from typing import Any, ClassVar

import maxminddb
import netaddr

from powergslb.client import ClientGeo

__all__ = ['GeoIPReader']


class GeoIPReader:
    """Resolves a client IP to its country and continent from the GeoIP database.

    The GeoIP database is opened once at startup and is thread-safe for concurrent lookups.
    With no database configured every lookup yields ClientGeo().

    :param geoip_config: The [geoip] config section; its 'database' option is the GeoIP database path.
    """

    CONTINENT_CODES: ClassVar[frozenset[str]] = frozenset({'AF', 'AN', 'AS', 'EU', 'NA', 'OC', 'SA'})

    # ISO 3166-1 alpha-2 (249 assigned) plus XK, the user-assigned code MaxMind / DB-IP emit for Kosovo.
    COUNTRY_CODES: ClassVar[frozenset[str]] = frozenset({
        'AD', 'AE', 'AF', 'AG', 'AI', 'AL', 'AM', 'AO', 'AQ', 'AR', 'AS', 'AT', 'AU', 'AW', 'AX', 'AZ', 'BA', 'BB',
        'BD', 'BE', 'BF', 'BG', 'BH', 'BI', 'BJ', 'BL', 'BM', 'BN', 'BO', 'BQ', 'BR', 'BS', 'BT', 'BV', 'BW', 'BY',
        'BZ', 'CA', 'CC', 'CD', 'CF', 'CG', 'CH', 'CI', 'CK', 'CL', 'CM', 'CN', 'CO', 'CR', 'CU', 'CV', 'CW', 'CX',
        'CY', 'CZ', 'DE', 'DJ', 'DK', 'DM', 'DO', 'DZ', 'EC', 'EE', 'EG', 'EH', 'ER', 'ES', 'ET', 'FI', 'FJ', 'FK',
        'FM', 'FO', 'FR', 'GA', 'GB', 'GD', 'GE', 'GF', 'GG', 'GH', 'GI', 'GL', 'GM', 'GN', 'GP', 'GQ', 'GR', 'GS',
        'GT', 'GU', 'GW', 'GY', 'HK', 'HM', 'HN', 'HR', 'HT', 'HU', 'ID', 'IE', 'IL', 'IM', 'IN', 'IO', 'IQ', 'IR',
        'IS', 'IT', 'JE', 'JM', 'JO', 'JP', 'KE', 'KG', 'KH', 'KI', 'KM', 'KN', 'KP', 'KR', 'KW', 'KY', 'KZ', 'LA',
        'LB', 'LC', 'LI', 'LK', 'LR', 'LS', 'LT', 'LU', 'LV', 'LY', 'MA', 'MC', 'MD', 'ME', 'MF', 'MG', 'MH', 'MK',
        'ML', 'MM', 'MN', 'MO', 'MP', 'MQ', 'MR', 'MS', 'MT', 'MU', 'MV', 'MW', 'MX', 'MY', 'MZ', 'NA', 'NC', 'NE',
        'NF', 'NG', 'NI', 'NL', 'NO', 'NP', 'NR', 'NU', 'NZ', 'OM', 'PA', 'PE', 'PF', 'PG', 'PH', 'PK', 'PL', 'PM',
        'PN', 'PR', 'PS', 'PT', 'PW', 'PY', 'QA', 'RE', 'RO', 'RS', 'RU', 'RW', 'SA', 'SB', 'SC', 'SD', 'SE', 'SG',
        'SH', 'SI', 'SJ', 'SK', 'SL', 'SM', 'SN', 'SO', 'SR', 'SS', 'ST', 'SV', 'SX', 'SY', 'SZ', 'TC', 'TD', 'TF',
        'TG', 'TH', 'TJ', 'TK', 'TL', 'TM', 'TN', 'TO', 'TR', 'TT', 'TV', 'TW', 'TZ', 'UA', 'UG', 'UM', 'US', 'UY',
        'UZ', 'VA', 'VC', 'VE', 'VG', 'VI', 'VN', 'VU', 'WF', 'WS', 'YE', 'YT', 'ZA', 'ZM', 'ZW', 'XK',
    })

    def __init__(self, geoip_config: dict[str, Any]) -> None:
        self._reader: Any = None
        database = geoip_config.get('database')
        if not database:
            logging.info('geoip database not configured')
            return
        try:
            self._reader = maxminddb.open_database(database)
            logging.info('geoip database loaded: %s', database)
        except (OSError, maxminddb.InvalidDatabaseError) as e:
            logging.error('geoip database %s unavailable: %s: %s', database, type(e).__name__, e)

    @staticmethod
    def parse_geo_token(token: str) -> tuple[str, str] | None:
        """Parse a single token as a geo selector.

        :param token: A single token.
        :returns: ('country', 'DE'), ('continent', 'EU'), or None when the token has no geo prefix.
        :raises ValueError: When the token has a geo prefix but an unrecognized value (e.g. country:ZZ, continent:XX).
        """
        prefix, sep, value = token.partition(':')
        if not sep:
            return None

        kind = prefix.lower()
        value = value.upper()

        if kind == 'country':
            if value in GeoIPReader.COUNTRY_CODES:
                return 'country', value
            raise ValueError(f'country geo token invalid: {token}')

        if kind == 'continent':
            if value in GeoIPReader.CONTINENT_CODES:
                return 'continent', value
            raise ValueError(f'continent geo token invalid: {token}')

        return None

    def lookup(self, ip: netaddr.IPAddress | None) -> ClientGeo:
        """Resolve a client IP to its ISO country code and continent code.

        :param ip: The client IP address.
        :returns: The resolved ClientGeo, or ClientGeo() when there is no database or lookup failed.
        """
        if self._reader is None or ip is None:
            return ClientGeo()

        try:
            record = self._reader.get(ip.format())
        except ValueError:
            return ClientGeo()
        if not isinstance(record, dict):
            return ClientGeo()

        return ClientGeo(record.get('country', {}).get('iso_code'),
                         record.get('continent', {}).get('code'))
