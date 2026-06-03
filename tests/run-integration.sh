#!/bin/bash
# Run the PowerGSLB integration test suite.
#
# Builds the Docker image, starts a container, waits until the service is
# ready, runs pytest, then removes the container on success. On failure the
# container is left running so logs can be inspected.
#
# Usage: tests/run-integration.sh [options] [pytest-args...]
#
# Options:
#   --no-build   Skip docker build and reuse an existing powergslb:dev image.
#
# Examples:
#   tests/run-integration.sh
#   tests/run-integration.sh --no-build
#   tests/run-integration.sh tests/integration/test_dns_backend.py -v

set -euo pipefail

IMAGE=powergslb:dev
CONTAINER=powergslb
BUILD=1
PYTEST_ARGS=()

for arg in "$@"; do
    if [[ "${arg}" == "--no-build" ]]; then
        BUILD=0
    else
        PYTEST_ARGS+=("${arg}")
    fi
done

if [[ "${#PYTEST_ARGS[@]}" -eq 0 ]]; then
    PYTEST_ARGS=(tests/integration)
fi

# leave the container running so logs can be inspected, then exit non-zero
leave_running() {
    echo
    echo "$1 - container '${CONTAINER}' left running for inspection."
    echo "  docker exec -it ${CONTAINER} journalctl -u powergslb"
    echo "  docker exec -it ${CONTAINER} journalctl -u mariadb"
    echo "  docker exec -it ${CONTAINER} journalctl -u pdns"
    echo "  docker rm -f ${CONTAINER}  # when done"
    exit 1
}
trap 'leave_running Interrupted' INT TERM

# remove stale container if present
if docker inspect "${CONTAINER}" &>/dev/null; then
    echo "Removing stale container '${CONTAINER}'..."
    docker rm -f "${CONTAINER}"
fi

# build
if [[ "${BUILD}" -eq 1 ]]; then
    echo "Building ${IMAGE}..."
    docker build -f docker/Dockerfile --force-rm --no-cache -t "${IMAGE}" .
else
    if ! docker image inspect "${IMAGE}" &>/dev/null; then
        echo "Image '${IMAGE}' not found. Run without --no-build first." >&2
        exit 1
    fi
    echo "Reusing existing image '${IMAGE}'."
fi

# start
echo "Starting container '${CONTAINER}'..."
docker run -d --name "${CONTAINER}" --privileged \
    -e POWERGSLB_SERVER_ADDRESS=0.0.0.0 \
    -e POWERGSLB_MONITOR_UPDATE_INTERVAL=2 \
    --tmpfs /run --tmpfs /tmp \
    "${IMAGE}"

# inspect
CONTAINER_IP=$(docker inspect \
    -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${CONTAINER}")
echo "Container IP: ${CONTAINER_IP}"

export POWERGSLB_URL="http://${CONTAINER_IP}:8080"
export POWERGSLB_ADMIN_URL="https://${CONTAINER_IP}:443"
export POWERGSLB_DIG_ADDR="${CONTAINER_IP}"
export POWERGSLB_CONTAINER="${CONTAINER}"  # lets the lifecycle; other tests ignore it

# wait for ready
echo -n "Waiting for service..."
for i in {1..60}; do
    if curl -sf "${POWERGSLB_URL}/dns/lookup/example.com./SOA" > /dev/null 2>&1; then
        echo " ready (${i}s)"
        break
    fi
    echo -n "."
    sleep 2
    if [[ "${i}" -eq 60 ]]; then
        echo " timed out"
        docker logs "${CONTAINER}"
        exit 1
    fi
done

# run tests
echo "Running: .venv/bin/pytest ${PYTEST_ARGS[*]}"
if .venv/bin/pytest "${PYTEST_ARGS[@]}"; then
    echo
    echo "All tests passed - removing container."
    docker rm -f "${CONTAINER}"
else
    leave_running "Tests failed"
fi
