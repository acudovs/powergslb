rm -fr /etc/systemd/system/*.wants
rm -f /usr/lib/systemd/system/*.wants/*
ln -rst /usr/lib/systemd/system/sockets.target.wants /usr/lib/systemd/system/dbus.socket
ln -rst /usr/lib/systemd/system/sockets.target.wants /usr/lib/systemd/system/systemd-journald.socket
ln -rst /usr/lib/systemd/system/sockets.target.wants /usr/lib/systemd/system/systemd-shutdownd.socket
ln -rst /usr/lib/systemd/system/sysinit.target.wants /usr/lib/systemd/system/systemd-tmpfiles-setup.service
ln -rst /usr/lib/systemd/system/timers.target.wants /usr/lib/systemd/system/systemd-tmpfiles-clean.timer

mkdir -p /etc/systemd/system/multi-user.target.wants
ln -s /usr/lib/systemd/system/mariadb.service /etc/systemd/system/multi-user.target.wants/mariadb.service
ln -s /usr/lib/systemd/system/pdns.service /etc/systemd/system/multi-user.target.wants/pdns.service
ln -s /usr/lib/systemd/system/powergslb.service /etc/systemd/system/multi-user.target.wants/powergslb.service
ln -s /usr/lib/systemd/system/stunnel@.service /etc/systemd/system/multi-user.target.wants/stunnel@powergslb.service
