# pylint: disable=missing-function-docstring

"""End-to-end DNS tests through the real PowerDNS server.

Unlike the other suites, which call the PowerGSLB HTTP remotebackend directly, these query PowerDNS on port 53 with dig
and so exercise the full path: dig -> PowerDNS -> remotebackend protocol -> PowerGSLB -> database. This confirms
PowerDNS parses our JSON responses and assembles correct wire-format answers for each record type. dig is required; the
suite fails loudly if it is not installed on the host running pytest.
"""

import shutil
import subprocess

_DIG = shutil.which('dig')


def _dig(dns_addr: str, name: str, qtype: str, *extra: str) -> list[str]:
    """Run dig +short for name/qtype against dns_addr and return its lines."""
    result = subprocess.run(
        [str(_DIG), f'@{dns_addr}', name, qtype, '+short', '+time=2', '+tries=1', *extra],
        capture_output=True, text=True, timeout=10, check=True)
    return result.stdout.strip().splitlines()


def test_dig_a_record(dns_addr: str) -> None:
    addresses = _dig(dns_addr, 'example.com', 'A')
    assert len(addresses) > 0
    assert all(addr.startswith('192.0.2.') for addr in addresses)


def test_dig_aaaa_record(dns_addr: str) -> None:
    addresses = _dig(dns_addr, 'example.com', 'AAAA')
    assert len(addresses) > 0
    assert all(addr.startswith('2001:db8::') for addr in addresses)


def test_dig_ns_record(dns_addr: str) -> None:
    ns_records = _dig(dns_addr, 'example.com', 'NS')
    assert len(ns_records) == 4
    assert all('example.com' in ns for ns in ns_records)


def test_dig_soa_record(dns_addr: str) -> None:
    lines = _dig(dns_addr, 'example.com', 'SOA')
    assert len(lines) == 1
    assert lines[0].startswith('ns1.example.com. hostmaster.example.com.')


def test_dig_cname_record(dns_addr: str) -> None:
    lines = _dig(dns_addr, 'www.example.com', 'CNAME')
    assert lines == ['example.com.']


def test_dig_mx_record(dns_addr: str) -> None:
    # PowerDNS splits the leading priority into its own wire field; +short
    # prints 'priority target.' lines
    lines = _dig(dns_addr, 'example.com', 'MX')
    assert len(lines) == 3
    assert {line.split()[0] for line in lines} == {'10', '20', '30'}
    assert all(line.split()[1].endswith('example.com.') for line in lines)


def test_dig_txt_record(dns_addr: str) -> None:
    lines = _dig(dns_addr, 'example.com', 'TXT')
    assert len(lines) == 1
    # dig wraps TXT data in double quotes
    assert lines[0] == '"v=spf1 ip4:192.0.2.0/24 2001:db8::/32 ~all"'


def test_dig_srv_record(dns_addr: str) -> None:
    lines = _dig(dns_addr, '_sip._tcp.example.com', 'SRV')
    assert len(lines) == 1
    # priority weight port target.
    assert lines[0] == '10 100 5060 sip.example.com.'


def test_dig_unknown_name_returns_nothing(dns_addr: str) -> None:
    # A name with no records yields an empty +short answer (NXDOMAIN / NODATA)
    lines = _dig(dns_addr, 'no.such.name.example.com', 'A')
    assert lines == []
