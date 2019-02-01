FROM docker.io/centos:7.6.1810
ARG VERSION
COPY docker/rootfs /
RUN /build
ENV container docker
CMD ["/usr/lib/systemd/systemd"]
STOPSIGNAL SIGRTMIN+3
