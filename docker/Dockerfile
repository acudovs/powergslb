# Stage 1: builder
FROM registry.access.redhat.com/ubi10/ubi AS builder

WORKDIR /build
COPY pyproject.toml requirements.txt ./
COPY src src

RUN python3 -m venv --copies --system-site-packages --upgrade-deps /opt/powergslb && \
    /opt/powergslb/bin/pip install -r requirements.txt && \
    /opt/powergslb/bin/pip install --no-deps .

# Stage 2: runtime
FROM registry.access.redhat.com/ubi10/ubi-init

EXPOSE 53/tcp 53/udp 443/tcp

ARG TZ=UTC

COPY --from=builder /opt/powergslb /opt/powergslb
COPY database /usr/share/powergslb
COPY docker/rootfs /

RUN /docker.build
