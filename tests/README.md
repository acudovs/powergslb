# PowerGSLB Test Suite

## Contents

- [Layout](#layout)
- [Create virtual environment](#create-virtual-environment)
- [Install dev dependencies and project editable](#install-dev-dependencies-and-project-editable)
- [Linting](#linting)
- [Unit tests with coverage](#unit-tests-with-coverage)
- [Integration tests](#integration-tests)
    - [Manual steps](#manual-steps)
    - [Running against a non-default host](#running-against-a-non-default-host)
    - [Running against a non-default database](#running-against-a-non-default-database)
    - [Debugging](#debugging)

---

## Layout

```
tests/
├── integration/                        end-to-end tests against a running Docker container
│   ├── conftest.py                     W2UIClient/DNSClient helpers and fixtures: base_url, admin_url, dns_addr,
│   │                                   require_container (autouse), w2ui, dns, base_record, cleanup
│   ├── test_admin.py                   admin HTTPS API: CRUD, search, sort, pagination, static files, malformed input
│   ├── test_dns_backend.py             DNS HTTP backend: record types, routing, headers, getAllDomains
│   ├── test_dns_records.py             records via admin: disabled, views, geo, weight, routing policies, IPv6
│   ├── test_health.py                  static health status reporting and DNS consistency
│   ├── test_lifecycle.py               systemctl stop/restart: no SIGKILL, clean rebind (needs POWERGSLB_CONTAINER)
│   ├── test_monitor_health.py          active fall/rise lifecycle, interpolation, bad-config resilience, all-down rule
│   ├── test_monitor_types.py           all five check types: icmp, tcp, http, tls, exec
│   ├── test_powerdns.py                DNS responses via PowerDNS, A/AAAA/NS/SOA/CNAME/MX/TXT/SRV (requires dig)
│   └── test_schema_constraints.py      raw-SQL constraints/triggers, GC, longest-zone-match (needs POWERGSLB_CONTAINER)
└── unit/                               in-process unit tests (no container required); mirrors src/powergslb/ layout
    ├── test_build_backend.py           build_backend/backend.py: admin-asset pre-compression, .gz/.br, keep-smaller
    ├── test_main.py                    entry point: argument parsing, thread wiring, SystemService startup
    ├── test_version.py                 version constant is a semver string
    ├── client/
    │   ├── test_context.py            ClientContext: carries the pre-parsed client network plus a mutable geo
    │   └── test_geo.py                ClientGeo: defaults to unknown, equality
    ├── database/mysql/
    │   ├── test_database.py            MySQLDatabase: SQL flattener, context manager, autocommit, result shaping
    │   ├── test_powerdns.py            PowerDNSMixIn SQL builders: gslb_checks/gslb_domains/gslb_records
    │   ├── test_tables.py              Table SQL/behavior: search/sort/paging pipeline, CRUD, records/users/status
    │   └── test_w2ui.py                W2UIMixIn router: token rejection, per-method delegation smoke tests
    ├── monitor/
    │   ├── check/
    │   │   ├── test_base.py            Check: type registry, create() validation branches, timeout clamp
    │   │   ├── test_exec.py            ExecCheck.execute(): subprocess exit code
    │   │   ├── test_http.py            HttpCheck.execute(): 2xx vs non-2xx, body drained
    │   │   ├── test_icmp.py            IcmpCheck.execute(): ping alive, privileged flag, unknown host
    │   │   ├── test_none.py            NoCheck: skip flag set, base defaults, execute() always healthy
    │   │   ├── test_tcp.py             TcpCheck.execute(): socket connect
    │   │   ├── test_thread.py          CheckThread: rise/fall debounce, task() dispatch
    │   │   └── test_tls.py             TlsCheck.execute(): TLS handshake, tls_verify toggle, SNI/host override
    │   ├── test_monitor.py             MonitorManager: parse/build, status cleanup, thread lifecycle, task()
    │   ├── test_status.py              StatusRegistry/StatusWriter health set: add/remove/is_down/retain/get_writer
    │   └── test_thread.py              AbstractThread: run loop, daemon flag, graceful shutdown
    ├── routing/
    │   ├── test_base.py                RoutingPolicy: registry, create() validation, frozen, resolve() lru_cache
    │   ├── test_round_robin.py         RoundRobin.select(): highest tier, max_answers cap/subsample
    │   ├── test_sticky_hash.py         StickyHash: masked network, salt-free hash, HRW pick, bounded divergence
    │   └── test_weighted_random.py     WeightedRandom: _weighted_pick known draws, all-zero equal pick
    ├── server/http/
    │   ├── test_server.py              HTTPServerManager: config unpacking, bundled-resources root, plain/TLS run()
    │   └── handler/
    │       ├── test_admin.py           AdminRequestHandler: auth, route, w2ui dispatch, static assets
    │       ├── test_powerdns.py        PowerDNSRequestHandler: header override, route, view/health/policy pipeline
    │       ├── test_queryparser.py     w2ui query-string parser: flat/nested/indexed/array forms, helpers
    │       ├── test_request.py         HTTPRequestHandler base: handle() errors, body, route skeleton, writers
    │       ├── test_request_head.py    HEAD over a real socket: always 404, never serves static metadata
    │       └── test_request_routes.py  cross-port routing: each role 404s the other surface
    ├── system/
    │   ├── test_config.py              TOML config: typed values, items(), env overrides, singleton
    │   ├── test_password.py            crypt(3) SHA-512 helpers: $6$ format, random salt, verify accept/reject
    │   ├── test_service.py             SystemService thread supervision: exit non-zero when a thread dies
    │   └── test_thread.py              ServiceThread Protocol: structural conformance, isinstance checks
    └── view/
        ├── test_geoip.py               GeoIPReader: parse_geo_token classes, inert without DB, IP->ClientGeo
        └── test_rule.py                ViewRule: compile/cache, CIDR + geo match, geo resolved at most once
```

---

## Create virtual environment

```bash
python3 -m venv --copies --system-site-packages --upgrade-deps .venv
```

---

## Install dev dependencies and project editable

```bash
.venv/bin/pip install --group dev --editable .
```

---

## Linting

```bash
.venv/bin/pylint src tests
.venv/bin/mypy src tests
```

---

## Unit tests with coverage

In-process tests under `tests/unit/` that import the package directly and need no Docker container. Run them under
coverage - the integration tests exercise the service inside the Docker container (a separate process), so only the
unit tests contribute to coverage. The unit tests cover the package in full (100%).

```bash
.venv/bin/coverage run --source=src -m pytest tests/unit
.venv/bin/coverage report -m

# browsable report under htmlcov/
.venv/bin/coverage html
```

To run the tests on their own, without coverage:

```bash
.venv/bin/pytest tests/unit -v
```

---

## Integration tests

Integration tests run against a live Docker container. The fastest way is the helper script, which handles the full
container lifecycle:

```bash
# Build image and run tests
tests/run-integration.sh

# Reuse existing image (skip docker build)
tests/run-integration.sh --no-build

# Pass extra pytest args
tests/run-integration.sh --no-build tests/integration/test_dns_backend.py -v
```

The container is removed automatically on success. On failure, it is left running so you can inspect logs
(see Debugging below).

### Manual steps

Use this when you want full control.

```bash
# 1. Build the image (once; skip on subsequent runs)
docker build -f docker/Dockerfile --force-rm --no-cache -t powergslb:dev .

# 2. Start - bind backend to 0.0.0.0 so it is reachable on the container IP
docker run -d --name powergslb --privileged \
    -e POWERGSLB_SERVER_ADDRESS=0.0.0.0 \
    -e POWERGSLB_MONITOR_UPDATE_INTERVAL=2 \
    --tmpfs /run --tmpfs /tmp \
    powergslb:dev

# 3. Inspect - get container IP
CONTAINER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' powergslb)

# 4. Export test env vars
export POWERGSLB_URL=http://${CONTAINER_IP}:8080
export POWERGSLB_ADMIN_URL=https://${CONTAINER_IP}:443
export POWERGSLB_DIG_ADDR=${CONTAINER_IP}

# 5. Wait for ready
until curl -sf "${POWERGSLB_URL}/dns/lookup/example.com./SOA"; do sleep 2; done

# 6. Run tests
.venv/bin/pytest tests/integration -v

# 7. Cleanup (skip to keep the container alive for debugging)
docker rm -f powergslb
```

### Running against a non-default host

```bash
POWERGSLB_URL=http://192.168.1.10:8080 \
POWERGSLB_ADMIN_URL=https://192.168.1.10:443 \
POWERGSLB_DIG_ADDR=192.168.1.10 \
    .venv/bin/pytest tests/integration -v
```

### Running against a non-default database

The container ships with a bundled MariaDB and connects to it over a Unix socket. To point the service at an external
database instead, override the `[database]` config with `POWERGSLB_DATABASE_*` environment variables at `docker run`
time:

```bash
docker run -d --name powergslb --privileged \
    -e POWERGSLB_SERVER_ADDRESS=0.0.0.0 \
    -e POWERGSLB_MONITOR_UPDATE_INTERVAL=2 \
    -e POWERGSLB_DATABASE_HOST=192.168.1.20 \
    -e POWERGSLB_DATABASE_PORT=3306 \
    -e POWERGSLB_DATABASE_UNIX_SOCKET= \
    -e POWERGSLB_DATABASE_USER=powergslb \
    -e POWERGSLB_DATABASE_PASSWORD=secret \
    -e POWERGSLB_DATABASE_DATABASE=powergslb \
    --tmpfs /run --tmpfs /tmp \
    powergslb:dev
```

The shipped config sets `unix_socket`, which takes precedence over `host`/`port`. Set `POWERGSLB_DATABASE_UNIX_SOCKET=`
(empty) to use TCP when connecting to a remote host.

The external database needs the schema and seed data loaded first. Both files live under `database/`:

```bash
mariadb -h 192.168.1.20 -u powergslb -p powergslb \
    < database/scheme.sql
mariadb -h 192.168.1.20 -u powergslb -p powergslb \
    < database/data.sql
```

### Debugging

If tests fail, the container is left running:

```bash
docker exec -it powergslb journalctl -u powergslb
docker exec -it powergslb journalctl -u mariadb
docker exec -it powergslb journalctl -u pdns
```
