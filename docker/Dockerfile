FROM docker.io/centos:7.6.1810
ENV container docker
CMD ["/usr/lib/systemd/systemd"]
STOPSIGNAL SIGRTMIN+3

ARG TZ=UTC
ARG VERSION

COPY docker/rootfs /
RUN /docker.build
