FROM docker.io/centos:7.7.1908
ENV container docker
CMD ["/usr/lib/systemd/systemd"]
STOPSIGNAL SIGRTMIN+3

EXPOSE 53/tcp
EXPOSE 53/udp
EXPOSE 443/tcp

ARG TZ=UTC
ARG VERSION

COPY docker/rootfs /
RUN /docker.build
