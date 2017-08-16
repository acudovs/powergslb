# PowerGSLB - PowerDNS Remote GSLB Backend

PowerGSLB is a simple DNS based Global Server Load Balancing (GSLB) solution.


## Table of Contents

* [Main features](#main-features)
* [Database diagram](#database-diagram)
* [Class diagram](#class-diagram)
* [Web based administration interface](#web-based-administration-interface)
* [Installation on CentOS 7](#installation-on-centos-7)
   * [Setup PowerGSLB, PowerDNS and stunnel](#setup-powergslb-powerdns-and-stunnel)
   * [Setup MariaDB](#setup-mariadb)
   * [Start services](#start-services)
   * [Test PowerGSLB](#test-powergslb)
   * [Web based administration interface](#web-based-administration-interface-1)
* [Building PowerGSLB RPM packages](#building-powergslb-rpm-packages)
* [Using PowerGSLB Docker image](#using-powergslb-docker-image)
* [Building PowerGSLB Docker image](#building-powergslb-docker-image)


## Main features

* Quick installation and setup
* Written in Python 2.7
* Built as PowerDNS Authoritative Server [Remote Backend](https://doc.powerdns.com/3/authoritative/backend-remote/)
* Web based administration interface using [w2ui](http://w2ui.com/)
* HTTPS support for the webserver using [stunnel](https://www.stunnel.org/)
* DNS GSLB configuration stored in a MySQL / MariaDB database
* Master-Slave DNS GSLB using native MySQL / MariaDB [replication](https://dev.mysql.com/doc/refman/5.5/en/replication.html)
* Multi-Master DNS GSLB using native MySQL / MariaDB [Galera Cluster](http://galeracluster.com/)
* Modular architecture
* Multithreaded architecture
* Systemd status and watchdog support
* Extendable health checks:
    * ICMP ping
    * TCP connect
    * HTTP request
    * Arbitrary command execution
* Fallback if all the checks failed
* Weighted (priority) records
* Per record client IP / subnet persistence
* DNS GSLB views support
* All-in-one Docker image

*Please report bugs and request new features!*


## Database diagram

![](https://github.com/AlekseyChudov/powergslb/blob/master/images/database.png?raw=true)


## Class diagram

![](https://github.com/AlekseyChudov/powergslb/blob/master/images/class-diagram.png?raw=true)


## Web based administration interface

Status grid
![](https://github.com/AlekseyChudov/powergslb/blob/master/images/web-status.png?raw=true)

Advanced search
![](https://github.com/AlekseyChudov/powergslb/blob/master/images/web-search.png?raw=true)

Add new record
![](https://github.com/AlekseyChudov/powergslb/blob/master/images/web-form.png?raw=true)

[More images](https://github.com/AlekseyChudov/powergslb/tree/master/images)


## Installation on CentOS 7

### Setup PowerGSLB, PowerDNS and stunnel

```shell
yum -y update
yum -y install epel-release
yum -y install gcc python-devel python-pip

pip install pyping subprocess32

VERSION=1.6.4
yum -y install \
    "https://github.com/AlekseyChudov/powergslb/releases/download/$VERSION/powergslb-$VERSION-1.el7.centos.noarch.rpm" \
    "https://github.com/AlekseyChudov/powergslb/releases/download/$VERSION/powergslb-admin-$VERSION-1.el7.centos.noarch.rpm" \
    "https://github.com/AlekseyChudov/powergslb/releases/download/$VERSION/powergslb-pdns-$VERSION-1.el7.centos.noarch.rpm" \
    "https://github.com/AlekseyChudov/powergslb/releases/download/$VERSION/powergslb-stunnel-$VERSION-1.el7.centos.noarch.rpm"

sed -i 's/^password = .*/password = your-database-password-here/g' /etc/powergslb/powergslb.conf

cp /etc/pdns/pdns.conf /etc/pdns/pdns.conf~
cp "/usr/share/doc/powergslb-pdns-$VERSION/pdns/pdns.conf" /etc/pdns/pdns.conf
```

### Setup MariaDB

```shell
yum -y install mariadb-server

sed -i '/\[mysqld\]/a bind-address=127.0.0.1\ncharacter_set_server=utf8' /etc/my.cnf.d/server.cnf

systemctl enable mariadb.service
systemctl start mariadb.service
systemctl status mariadb.service

mysql_secure_installation

VERSION=1.6.4
mysql -p << EOF
CREATE DATABASE powergslb;
GRANT ALL ON powergslb.* TO powergslb@localhost IDENTIFIED BY 'your-database-password-here';
USE powergslb;
source /usr/share/doc/powergslb-$VERSION/database/scheme.sql
source /usr/share/doc/powergslb-$VERSION/database/data.sql
EOF
```

### Start services

```shell
systemctl enable powergslb.service pdns.service stunnel@powergslb
systemctl start powergslb.service pdns.service stunnel@powergslb
systemctl status powergslb.service pdns.service stunnel@powergslb
```

### Test PowerGSLB

```shell
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


## Building PowerGSLB RPM packages

You should always create RPM packages in a clean environment and preferably on a separate machine!

Please read [How to create an RPM package](https://fedoraproject.org/wiki/How_to_create_an_RPM_package).
```shell
yum -y update
yum -y install @Development\ Tools

VERSION=1.6.4
curl "https://codeload.github.com/AlekseyChudov/powergslb/tar.gz/$VERSION" > "powergslb-$VERSION.tar.gz"
rpmbuild -tb --define "version $VERSION" "powergslb-$VERSION.tar.gz"
```

Upon successful completion you will have four packages
```
~/rpmbuild/RPMS/noarch/powergslb-$VERSION-1.el7.centos.noarch.rpm
~/rpmbuild/RPMS/noarch/powergslb-admin-$VERSION-1.el7.centos.noarch.rpm
~/rpmbuild/RPMS/noarch/powergslb-pdns-$VERSION-1.el7.centos.noarch.rpm
~/rpmbuild/RPMS/noarch/powergslb-stunnel-$VERSION-1.el7.centos.noarch.rpm
```


## Using PowerGSLB Docker image

For quick setup, you can pull all-in-one Docker image from docker.io.

```
VERSION=1.6.4

docker pull docker.io/alekseychudov/powergslb:"$VERSION"

docker run -d --name powergslb --hostname powergslb docker.io/alekseychudov/powergslb:"$VERSION"

docker inspect --format='{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' powergslb

docker exec -it powergslb bash

docker stop powergslb
```


## Building PowerGSLB Docker image

To can create an all-in-one Docker image.

```
VERSION=1.6.4

docker build -f docker/Dockerfile --build-arg VERSION="$VERSION" \
    --force-rm --no-cache -t powergslb:"$VERSION" https://github.com/AlekseyChudov/powergslb.git
```
