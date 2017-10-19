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
* [Install using VirtualEnv](#install-using-virtualenv)
* [Nginx as reverse proxy] (#nginx-as-reverse-proxy)
* [LB mode] (#lb-mode)
   * [Priority] (#priority)
   * [Topology] (#topology)
   * [Weighted Round Robin] (#weighted-round-robin)
   * [Topology Weighted Round Robin] (#topology-weighted-round-robin)
* [Tests] (#tests)

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

VERSION=1.6.5
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

VERSION=1.6.5
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

VERSION=1.6.5
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
VERSION=1.6.5

docker pull docker.io/alekseychudov/powergslb:"$VERSION"

docker run -d --name powergslb --hostname powergslb docker.io/alekseychudov/powergslb:"$VERSION"

docker inspect --format='{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' powergslb

docker exec -it powergslb bash

docker stop powergslb
```


## Building PowerGSLB Docker image

To create an all-in-one Docker image.

```
VERSION=1.6.5

docker build -f docker/Dockerfile --build-arg VERSION="$VERSION" \
    --force-rm --no-cache -t powergslb:"$VERSION" https://github.com/AlekseyChudov/powergslb.git
```

## Install using VirtualEnv

```
> virtualenv powergslb
> cd powergslb
> source bin/activate
```

```
> git clone https://github.com/AlekseyChudov/powergslb.git
> cd powergslb
> pip install -r requirements.txt
> sudo ln -s /home/powergslb/powergslb/current/powergslb/powergslb.service /etc/systemd/system/powergslb.service
> python setup.py install ; sudo service powergslb restart
```

## Nginx as reverse proxy

Example:


```
upstream powergslb {
  server localhost:8080;
}

server {
        listen 80;
        server_name powergslb.local;

        return 301 https://$host$request_uri;
}

server {
    server_name powergslb.local;

    # SSL configuration
    #
    listen 443 ssl;
    listen [::]:443 ssl;
    #
    # Note: You should disable gzip for SSL traffic.
    # See: https://bugs.debian.org/773332
    #
    # Read up on ssl_ciphers to ensure a secure configuration.
    # See: https://bugs.debian.org/765782
    #
    # Self signed certs generated by the ssl-cert package
    # Don't use them in a production server!
    #
    #include snippets/snakeoil.conf;
    ssl_certificate /etc/ssl/certs/ssl-cert-snakeoil.pem;
    ssl_certificate_key     /etc/ssl/private/ssl-cert-snakeoil.key;

    location / {
        proxy_pass              http://powergslb;
        proxy_set_header        Host $host;
        proxy_set_header        Referer "";
        proxy_set_header        X-Remotebackend-Real-Remote $remote_addr;
        proxy_set_header        X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version      1.1;
    }

    error_log /var/log/nginx/powergslb_error.log;
    access_log /var/log/nginx/powergslb_access.logd;
}
```

## LB mode

### Priority

Records with highest weigth are send, DNS LB is only efficient on A records.
On CNAME only first record will always be selected.

### Topology

Client IP and record content are compared to a topology map in order to extract a region.
If both region match, only these records will be selected.

Based on topology map like :

```
_lb_topology_map = {
  'region1': [ '10.10.0.0/16' ],
  'region2': [ '10.15.0.0/16' ],
  ...
}
```

If client IP is 10.10.0.1 and possible records are 10.10.0.10 and 10.15.0.10, only 10.10.0.10 will be sent.
If 10.10.0.10 is detected as down, 10.15.0.10 will then be send.

### Weighted Round Robin

One record is sent randomly based on available records respective weight.

Record probability = [record weight] / sum( [record weight] )

### Topology Weighted Round Robin

Mix both topology and weighted round robin (only for A records).

## Tests

You can set the client IP at the value you want using the http header X-Remotebackend-Real-Remote.

### Topology load-balancing:

clients IPs: 10.10.0.1 and 10.15.0.1
t.example.net: 2 A records 10.10.0.10 and 10.15.0.10

Client 10.10.0.1 receives 10.10.0.10:
```
curl --silent -H 'X-Remotebackend-Real-Remote: 10.10.0.1' -H 'host: powergslb.local' http://127.0.0.1:8080/dns/lookup/t.example.net/ANY | jq .
{
  "result": [
    {
      "content": "10.10.0.10",
      "qtype": "A",
      "qname": "t.example.net",
      "ttl": 10
    }
  ]
}
```

Client 10.15.0.1 receives 10.15.0.10:

```
> curl --silent -H 'X-Remotebackend-Real-Remote: 10.15.0.1' -H 'host: powergslb.local' http://127.0.0.1:8080/dns/lookup/t.example.net/ANY | jq .
{
  "result": [
    {
      "content": "10.15.0.10",
      "qtype": "A",
      "qname": "t.example.net",
      "ttl": 10
    }
  ]
}

```

If checks ara actives, when an IP is failing, the other IP will be delivered to every clients.

### Weighted Round Robin

Client IP: 192.168.0.1
wrr.example.net: A records 10.10.0.10 with a weight of 1 and 10.15.0.10 with a weight of 2

```
> curl --silent -H 'X-Remotebackend-Real-Remote: 192.168.0.1' -H 'host: powergslb.local' http://127.0.0.1:8080/dns/lookup/wrr.example.net/ANY | jq .
{
  "result": [
    {
      "content": "10.15.0.10",
      "qtype": "A",
      "qname": "wrr.example.net",
      "ttl": 10
    }
  ]
}
```

Client will receive 10.15.0.10 two times more than 10.10.0.10.