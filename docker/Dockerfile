FROM docker.io/centos:7.3.1611
ARG VERSION
COPY docker/rootfs /
RUN /build
ENV container docker
CMD ["/usr/lib/systemd/systemd"]
STOPSIGNAL SIGRTMIN+3
