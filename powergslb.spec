%define		powergslb_user	powergslb
%define		powergslb_group	powergslb
%define		powergslb_home	%{_sysconfdir}/powergslb

%define		stunnel_user	stunnel
%define		stunnel_group	stunnel
%define		stunnel_home	%{_sysconfdir}/stunnel


Name:		powergslb
Version:	%{version}
Release:	1%{?dist}
Summary:	PowerDNS Remote GSLB Backend

Group:		System Environment/Daemons
License:	MIT
URL:		https://github.com/AlekseyChudov/powergslb
Source0:	powergslb-%{version}.tar.gz

BuildArch:	noarch

BuildRequires:	python

Requires:	mysql-connector-python
Requires:	python
Requires:	python-netaddr
Requires:	systemd-python

%systemd_requires


%description
PowerGSLB is a simple DNS Global Server Load Balancing (GSLB) solution.

Main features:
* Quick installation and setup
* Written in Python 2.7
* Built as PowerDNS Authoritative Server Remote Backend
* Web based administration interface using w2ui
* HTTPS support for the webserver using stunnel
* DNS GSLB configuration stored in a MySQL / MariaDB database
* Master-Slave DNS GSLB using native MySQL / MariaDB replication
* Multi-Master DNS GSLB using native MySQL / MariaDB Galera Cluster
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


%package admin
Summary:	PowerGSLB web based administration interface


%package pdns
Summary:	PowerGSLB PowerDNS configuration

Requires:	pdns
Requires:	pdns-backend-remote


%package stunnel
Summary:	PowerGSLB stunnel configuration

Requires:	openssl
Requires:	powergslb
Requires:	stunnel


%description admin
PowerGSLB is a simple DNS Global Server Load Balancing (GSLB) solution.

This package contains the PowerGSLB web based administration interface.


%description pdns
PowerGSLB is a simple DNS Global Server Load Balancing (GSLB) solution.

This package contains the PowerGSLB PowerDNS configuration and dependencies.


%description stunnel
PowerGSLB is a simple DNS Global Server Load Balancing (GSLB) solution.

This package contains the PowerGSLB stunnel configuration and dependencies.


%prep
%setup -q


%install
python setup.py install -O1 --root=%{buildroot} --record=INSTALLED_FILES

install -D powergslb/powergslb %{buildroot}%{_sbindir}/powergslb
install -D powergslb/powergslb.conf %{buildroot}%{_sysconfdir}/powergslb/powergslb.conf
install -D powergslb/powergslb.service %{buildroot}%{_unitdir}/powergslb.service
install -d %{buildroot}%{_datarootdir}/powergslb
cp -rv admin %{buildroot}%{_datarootdir}/powergslb

install -D stunnel/powergslb.conf %{buildroot}%{_sysconfdir}/stunnel/powergslb.conf
install -D stunnel/stunnel@.service %{buildroot}%{_unitdir}/stunnel@.service


%pre
getent group %{powergslb_group} >/dev/null || \
    groupadd -r %{powergslb_group}

getent passwd %{powergslb_user} >/dev/null || \
    useradd -c "PowerGSLB daemon" -d %{powergslb_home} -g %{powergslb_group} -r \
            -s /sbin/nologin %{powergslb_user}


%pre stunnel
getent group %{stunnel_group} >/dev/null || \
    groupadd -r %{stunnel_group}

getent passwd %{stunnel_user} >/dev/null || \
    useradd -c "stunnel daemon" -d %{stunnel_home} -g %{stunnel_group} -r \
            -s /sbin/nologin %{stunnel_user}


%post
%systemd_post powergslb.service


%post stunnel
test -e %{_sysconfdir}/stunnel/powergslb.pem || \
    %{_sysconfdir}/pki/tls/certs/make-dummy-cert %{_sysconfdir}/stunnel/powergslb.pem && \
    chown root:%{stunnel_group} %{_sysconfdir}/stunnel/powergslb.pem && \
    chmod 0640 %{_sysconfdir}/stunnel/powergslb.pem

%systemd_post stunnel@powergslb.service


%preun
%systemd_preun powergslb.service


%preun stunnel
%systemd_preun stunnel@powergslb.service


%postun
%systemd_postun_with_restart powergslb.service


%postun stunnel
%systemd_postun_with_restart stunnel@powergslb.service


%files -f INSTALLED_FILES
%defattr(0644,root,root,0755)
%doc LICENSE README.md database images
%attr(0755,root,root) %{_sbindir}/powergslb
%attr(0750,root,%{powergslb_group}) %dir %{_sysconfdir}/powergslb
%attr(0640,root,%{powergslb_group}) %config(noreplace) %{_sysconfdir}/powergslb/powergslb.conf
%{_unitdir}/powergslb.service


%files admin
%defattr(0644,root,root,0755)
%doc LICENSE README.md
%{_datarootdir}/powergslb


%files pdns
%defattr(0644,root,root,0755)
%doc LICENSE README.md pdns


%files stunnel
%defattr(0644,root,root,0755)
%doc LICENSE README.md
%attr(0640,root,%{stunnel_group}) %config(noreplace) %{_sysconfdir}/stunnel/powergslb.conf
%{_unitdir}/stunnel@.service


%changelog
* Thu Feb 25 2016 Aleksey Chudov <aleksey.chudov@gmail.com>
- Initial spec file for CentOS 7
