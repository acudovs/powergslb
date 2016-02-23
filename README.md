# PowerGSLB - PowerDNS Remote GSLB Backend

PowerGSLB is a simple DNS Global Server Load Balancing (GSLB) solution.

Main features:
* Written in Python 2.7
* Built as PowerDNS Authoritative Server [Remote Backend] (https://doc.powerdns.com/3/authoritative/backend-remote/)
* Web based administration interface using [w2ui] (http://w2ui.com/)
* HTTPS support for the webserver using [stunnel] (https://www.stunnel.org/)
* DNS GSLB configuration stored in a MySQL / MariaDB database
* Multi-Master DNS GSLB using native MySQL / MariaDB [Galera Cluster] (http://galeracluster.com/)
* Multithreaded design
* Systemd status and watchdog support
* Extendable health checks:
    * ICMP ping
    * TCP connect
    * HTTP request
    * Arbitrary command execution
* Fallback if all the checks failed
* Weighted (priority) records
* Per record client IP / subnet persistence

*Please request new features!*

## Web based administration interface

Status page
![](https://github.com/AlekseyChudov/powergslb/blob/master/images/web-status.png?raw=true)

## Database diagram

![](https://github.com/AlekseyChudov/powergslb/blob/master/images/database.png?raw=true)

## Installation on CentOS 7

### Pre setup

```
yum -y update
yum -y install epel-release git

git clone https://github.com/AlekseyChudov/powergslb.git
cd powergslb
```

### Setup MariaDB

```
yum -y install mariadb-server

systemctl enable mariadb.service
systemctl start mariadb.service
systemctl status mariadb.service

mysql_secure_installation
```

### Setup PowerGSLB

```shell
yum -y install gcc mysql-connector-python python-devel python-netaddr python-pip systemd-python
pip install pyping subprocess32

mkdir -p /etc/powergslb /usr/share/powergslb
cp powergslb/powergslb.conf /etc/powergslb/
chmod 0600 /etc/powergslb/powergslb.conf
cp powergslb/powergslb /usr/sbin/
chmod 0755 /usr/sbin/powergslb
cp powergslb/powergslb.service /etc/systemd/system/powergslb.service
cp -r admin /usr/share/powergslb/
cp powergslb/queryparser.py /usr/lib/python2.7/site-packages/

mysql -p << EOF
CREATE DATABASE powergslb;
GRANT ALL ON powergslb.* TO powergslb@localhost IDENTIFIED BY 'your-database-password-here';
USE powergslb;
source database/scheme.sql
source database/data.sql
EOF

sed -i 's/^password = .*/password = your-database-password-here/g' /etc/powergslb/powergslb.conf

systemctl daemon-reload
systemctl enable powergslb.service
systemctl start powergslb.service
systemctl status powergslb.service
```

### Setup PowerDNS

```
yum -y install pdns pdns-backend-remote

cp /etc/pdns/pdns.conf /etc/pdns/pdns.conf~
cp pdns/pdns.conf /etc/pdns/

systemctl enable pdns.service
systemctl start pdns.service
systemctl status pdns.service
```

### Setup stunnel

```
yum -y install stunnel

useradd -c 'stunnel daemon' -d /etc/stunnel -r -s /sbin/nologin stunnel

openssl req -x509 -nodes -days 3650 -newkey rsa:2048 -keyout /etc/stunnel/powergslb.key -out /etc/stunnel/powergslb.crt

chown root:stunnel /etc/stunnel/powergslb.key
chmod 0640 /etc/stunnel/powergslb.key

cp stunnel/powergslb.conf /etc/stunnel/
cp stunnel/stunnel@.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable stunnel@powergslb
systemctl start stunnel@powergslb
systemctl status stunnel@powergslb
```

### Test PowerGSLB

```
yum -y install bind-utils

dig @127.0.0.1 example.com SOA
dig @127.0.0.1 example.com A
dig @127.0.0.1 example.com AAAA
dig @127.0.0.1 example.com ANY
```

### Web based administration interface

Open URL https://SERVER/admin/.

* Default username: admin
* Default password: admin
