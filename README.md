# PowerGSLB - PowerDNS Remote GSLB Backend

## Setup instructions for CentOS 7

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
yum -y install gcc mysql-connector-python python-devel python-pip systemd-python
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

dig @127.0.0.1 example.com ANY
```
