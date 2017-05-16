MYSQL_USER_PASSWORD="$(</dev/urandom tr -dc '[:alnum:]' | head -c32)"
MYSQL_ROOT_PASSWORD="$(</dev/urandom tr -dc '[:alnum:]' | head -c32)"

# Setup PowerGSLB, PowerDNS and stunnel

yum -y install gcc python-devel python-pip

pip install pyping subprocess32

yum -y --setopt=tsflags="" install \
    "https://github.com/AlekseyChudov/powergslb/releases/download/$VERSION/powergslb-$VERSION-1.el7.centos.noarch.rpm" \
    "https://github.com/AlekseyChudov/powergslb/releases/download/$VERSION/powergslb-admin-$VERSION-1.el7.centos.noarch.rpm" \
    "https://github.com/AlekseyChudov/powergslb/releases/download/$VERSION/powergslb-pdns-$VERSION-1.el7.centos.noarch.rpm" \
    "https://github.com/AlekseyChudov/powergslb/releases/download/$VERSION/powergslb-stunnel-$VERSION-1.el7.centos.noarch.rpm"

sed -i "s/^password = .*/password = $MYSQL_USER_PASSWORD/g" /etc/powergslb/powergslb.conf

cp /etc/pdns/pdns.conf /etc/pdns/pdns.conf~
cp "/usr/share/doc/powergslb-pdns-$VERSION/pdns/pdns.conf" /etc/pdns/pdns.conf

# Setup MariaDB

yum -y install mariadb-server

sed -i '/\[mysqld\]/a bind-address=127.0.0.1\ncharacter_set_server=utf8' /etc/my.cnf.d/server.cnf

su -s /bin/bash mysql /usr/libexec/mariadb-prepare-db-dir

mysqld_safe --basedir=/usr &
/usr/libexec/mariadb-wait-ready $$

mysql << EOF
CREATE DATABASE powergslb;
GRANT ALL ON powergslb.* TO powergslb@localhost IDENTIFIED BY '$MYSQL_USER_PASSWORD';
USE powergslb;
source /usr/share/doc/powergslb-$VERSION/database/scheme.sql
source /usr/share/doc/powergslb-$VERSION/database/data.sql
EOF

mysqladmin -u root password "$MYSQL_ROOT_PASSWORD"

cat << EOF > /root/.my.cnf
[client]
user=root
password=$MYSQL_ROOT_PASSWORD
EOF

pkill -f /usr/libexec/mysqld
