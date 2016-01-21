# PowerGSLB - PowerDNS Remote GSLB Backend

PowerGSLB is a simple DNS Global Server Load Balancing (GSLB) solution.

Main features:
* Written in Python 2.7
* Built as [PowerDNS Authoritative Server Remote Backend] (https://doc.powerdns.com/3/authoritative/backend-remote/)
* All DNS GSLB configuration stored in a MySQL / MariaDB database
* Extendable health checks:
    * ICMP ping
    * TCP connect
    * HTTP request
    * Arbitrary commands execution
* Fallback if all the checks failed
* Weighted (priority) records
* Client IP / Subnet persistence

*Please request new features!*

## Installation on CentOS 7

### Setup PowerDNS

```
yum -y update
yum -y install epel-release
yum -y install pdns pdns-backend-remote

cp /etc/pdns/pdns.conf /etc/pdns/pdns.conf~

cat << EOF > /etc/pdns/pdns.conf
setuid=pdns
setgid=pdns
launch=remote
remote-connection-string=http:url=http://127.0.0.1:8080/dns
EOF

systemctl daemon-reload
systemctl enable pdns.service
systemctl start pdns.service
systemctl status pdns.service
```

### Setup MariaDB

```
yum -y install mariadb-server

systemctl daemon-reload
systemctl enable mariadb.service
systemctl start mariadb.service
systemctl status mariadb.service

mysql_secure_installation
```

### Setup PowerGSLB

```shell
yum -y install gcc mysql-connector-python python-devel python-netaddr python-pip systemd-python
pip install pyping
pip install subprocess32

mkdir -p /etc/powergslb
cp powergslb.conf /etc/powergslb/
chmod 0600 /etc/powergslb/powergslb.conf
cp powergslb /usr/sbin/
cp powergslb.service /etc/systemd/system/

mysql -p << EOF
CREATE DATABASE powergslb;
GRANT SELECT ON powergslb.* TO powergslb@localhost IDENTIFIED BY 'your-database-password-here';
USE powergslb;
source powergslb.sql
EOF

systemctl daemon-reload
systemctl enable powergslb.service
systemctl start powergslb.service
systemctl status powergslb.service
```

### Test PowerDNS + PowerGSLB

```
yum -y install bind-utils

dig @127.0.0.1 example.com SOA
dig @127.0.0.1 example.com A
dig @127.0.0.1 example.com AAAA
dig @127.0.0.1 example.com ANY
```
