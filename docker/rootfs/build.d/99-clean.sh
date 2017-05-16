rm -f /anaconda-post.log
rm -f /etc/fstab
rm -f /etc/sysconfig/network-scripts/ifcfg-ens3
rm -f /root/anaconda-ks.cfg
rm -fr /var/log/anaconda

yum clean all
