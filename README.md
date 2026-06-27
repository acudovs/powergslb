# PowerGSLB - DNS Global Server Load Balancing

PowerGSLB is a DNS-based Global Server Load Balancing (GSLB) solution built as a PowerDNS Authoritative Server
[Remote Backend](https://doc.powerdns.com/authoritative/backends/remote.html). It continuously health-checks the
endpoints behind your DNS records and returns only the live ones, honoring weighted priorities, per-rrset routing
policies (round-robin, weighted-random, sticky-hash), and DNS views (CIDR and GeoIP).

## Table of Contents

* [Main features](#main-features)
* [Architecture](#architecture)
* [Quick start with the published Docker image](#quick-start-with-the-published-docker-image)
* [Persisting data](#persisting-data)
* [Upgrading](#upgrading)
* [Building the Docker image](#building-the-docker-image)
* [Manual setup](#manual-setup)
* [Configuration](#configuration)
* [Database](#database)
* [Web administration interface](#web-administration-interface)
* [Record selection](#record-selection)
    * [Views](#views)
    * [Weight (priority)](#weight-priority)
    * [Routing policies](#routing-policies)
    * [Disabled records](#disabled-records)
* [Health checks](#health-checks)
    * [General parameters](#general-parameters)
    * [Exec parameters](#exec-parameters)
    * [ICMP parameters](#icmp-parameters)
    * [HTTP parameters](#http-parameters)
    * [TCP parameters](#tcp-parameters)
    * [TLS parameters](#tls-parameters)
    * [Trust custom CA certificates](#trust-custom-ca-certificates)
* [API](#api)
* [Tests](#tests)
* [License](#license)

---

## Main features

* Written in Python 3.12
* Built as PowerDNS Authoritative Server [Remote Backend](https://doc.powerdns.com/authoritative/backends/remote.html)
* Modular and multithreaded architecture
* Systemd status and watchdog support
* Quick installation and setup
* All-in-one Docker image
* DNS GSLB configuration stored in a MySQL / MariaDB database
* Master-Slave DNS GSLB using native MySQL / MariaDB [replication](https://mariadb.com/kb/en/standard-replication/)
* Multi-Master DNS GSLB using native MySQL / MariaDB [Galera Cluster](https://galeracluster.com/)
* Web-based administration interface using [w2ui](https://github.com/vitmalina/w2ui)
* JSON [HTTP API](#api) for DNS queries and CRUD administration
* HTTPS support for the web server
* Record selection:
    * DNS GSLB views (CIDR and GeoIP)
    * Weighted (priority) records
    * Per-rrset routing policies: round-robin, weighted-random, sticky-hash
    * "All down = all up" so DNS never fails entirely during a full outage
* Extendable health checks:
    * Arbitrary command execution
    * ICMP ping
    * HTTP request
    * TCP connect
    * TLS connect

---

## Architecture

PowerGSLB runs a fixed set of cooperating service threads under a systemd-aware supervisor:

* **Monitor** - periodically reads the health-check configuration from the database, runs one check thread per
  monitored record, and maintains the in-memory set of records that are currently down. Rise / fall counters debounce
  flapping endpoints.
* **DNS interface** (default `127.0.0.1:8080`, plain HTTP) - implements the PowerDNS Remote Backend protocol. PowerDNS
  forwards each query here; PowerGSLB filters the candidate records by query type, view, and health status, then the
  rrset's routing policy chooses the answers (reading each record's weight), and returns a JSON DNS response.
* **Admin interface** (default `0.0.0.0:443`, HTTPS) - the web management UI and its CRUD API. Authenticates via HTTP
  Basic Auth against the database (crypt(3) SHA-512 hashes, verified in constant time).

The two HTTP surfaces are served by separate handler classes on separate ports, so the admin API is never reachable on
the DNS port and vice versa. The supervisor integrates with systemd (`READY=1`, watchdog, `STOPPING=1`) and shuts the
threads down cooperatively on `SIGTERM` / `SIGINT`.

### Class diagram

<details>
<summary>Click to expand the class diagram</summary>

The diagram below maps the application classes and their relationships. Standard-library and third-party base classes
are marked `<<stdlib>>` / `<<builtin>>`; the two helper modules that hold free functions are shown as `<<module>>`
pseudo-classes.

```mermaid
classDiagram
    direction TB

    %% ===== Entry point =====
    class PowerGSLB {
        +main()$ None
    }

    %% ===== system =====
    class Config {
        -dict _data
        +get(section, option, default) Any
        +items(section) dict
    }
    class _Section {
        -Config _config
        -str _section
        +get(option, default) Any
        +pop(option, default) Any
    }
    class SystemService {
        +Sequence~ServiceThread~ service_threads
        +float sleep_interval
        +float shutdown_timeout
        +start() None
        +systemd_notify(status, unset)$ None
        +watchdog_interval(default)$ float
    }
    class ServiceThread {
        <<Protocol>>
        +str name
        +start() None
        +is_alive() bool
        +shutdown(timeout) None
    }
    class password {
        <<module>>
        +hash_password(password)$ str
        +verify_password(password, stored)$ bool
    }

    %% ===== client =====
    class ClientGeo {
        +str | None country
        +str | None continent
    }
    class ClientContext {
        +IPAddress remote_ip
        +ClientGeo | None geo
    }

    %% ===== view =====
    class GeoIPReader {
        +frozenset~str~ CONTINENT_CODES$
        +frozenset~str~ COUNTRY_CODES$
        -Reader _reader
        +parse_geo_token(token)$ tuple | None
        +lookup(ip) ClientGeo
    }
    class ViewRule {
        -GeoIPReader | None _geoip$
        +tuple~IPNetwork~ cidrs
        +tuple~tuple~ geos
        +configure(geoip_config)$ None
        +resolve(rule)$ ViewRule
        +matches(context) bool
    }

    %% ===== monitor =====
    class AbstractThread {
        <<abstract>>
        +float sleep_interval
        +run() None
        +shutdown(timeout) None
        +task()* None
    }
    class MonitorManager {
        -dict~int,CheckThread~ _threads
        -StatusRegistry _status_registry
        +build_check(check)$ Check
        +task() None
        +shutdown(timeout) None
    }
    class StatusRegistry {
        -set~int~ _status
        +add(id) None
        +remove(id) None
        +is_down(id) bool
        +get_writer(id) StatusWriter
        +retain(valid_ids) set
    }
    class StatusWriter {
        -StatusRegistry _registry
        +int content_id
        +set_down() None
        +set_up() None
        +is_down() bool
    }
    class CheckThread {
        +Check check
        +StatusWriter status_writer
        +content_id() int
        +task() None
    }

    %% ===== monitor/check =====
    class Check {
        <<abstract dataclass>>
        -dict _registry$
        +str name$
        +bool skip$
        +int interval
        +int timeout
        +int fall
        +int rise
        +create(spec)$ Check
        +configure(options)$ None
        +execute()* bool
    }
    class NoCheck {
        +name = "none"
        +skip = True
        +execute() bool
    }
    class IcmpCheck {
        +name = "icmp"
        +bool privileged$
        +str ip
        +execute() bool
    }
    class TcpCheck {
        +name = "tcp"
        +str ip
        +int port
        +execute() bool
    }
    class TlsCheck {
        +name = "tls"
        +str ip
        +int port
        +bool tls_verify
        +str host
        +execute() bool
    }
    class HttpCheck {
        +name = "http"
        +str url
        +str method
        +str expected_status
        +str body_match
        +bool tls_verify
        +str host
        +execute() bool
    }
    class ExecCheck {
        +name = "exec"
        +list~str~ args
        +int expected_code
        +str output_match
        +bool redirect_error
        +execute() bool
    }

    %% ===== routing =====
    class RoutingPolicy {
        <<abstract>>
        +str name$
        +create(spec)$ RoutingPolicy
        +resolve(policy_json)$ RoutingPolicy
        +select(candidates, context)* list
    }
    class RoundRobin {
        +name = "round-robin"
        +int max_answers
        +select(candidates, context) list
    }
    class WeightedRandom {
        +name = "weighted-random"
        +int max_answers
        +select(candidates, context) list
    }
    class StickyHash {
        +name = "sticky-hash"
        +int max_answers
        +int ipv4_mask
        +int ipv6_mask
        +select(candidates, context) list
    }

    %% ===== server/http =====
    class HTTPServerManager {
        +str address
        +int port
        +bool ssl
        +str root
        +float keep_alive_timeout
        -type~HTTPRequestHandler~ _handler
        +run() None
        +shutdown(timeout) None
    }
    class _ThreadingHTTPServer {
    }
    class HTTPRequestHandler {
        <<abstract>>
        +str route$
        +Database database
        +StatusRegistry status_registry
        +bytes body
        +handle() None
        +do_GET() None
        +do_HEAD() None
        +do_POST() None
        +_handle_route()* None
    }
    class PowerDNSRequestHandler {
        +route = "dns"
        +content() str
    }
    class AdminRequestHandler {
        +route = "admin"
        -dict _commands$
        -set _data_tables$
        -dict _search_functions$
        +content() str
    }
    class queryparser {
        <<module>>
        +parse_query(query_string)$ dict
    }
    class QueryParserError {
        <<Exception>>
    }

    %% ===== database =====
    class MySQLDatabase {
        +Error
        +join_operation(op)$ str
        -_select(op, params) list
        -_modify(op, params) int
        -_execute_transaction(stmts) int
        +__enter__() Self
        +__exit__() None
    }
    class PowerDNSDatabaseMixIn {
        <<abstract>>
        +gslb_checks() list
        +gslb_domains(include_disabled) list
        +gslb_records(qname, qtype) list
    }
    class W2UIDatabaseMixIn {
        <<abstract>>
        +str password_mask$
        +check_user(user, password) list
        +get_*(recid) list
        +save_*(...) int
        +delete_*(ids) int
    }

    %% ===== stdlib bases =====
    class Thread { <<stdlib>> }
    class SimpleHTTPRequestHandler { <<stdlib>> }
    class HTTPServer { <<stdlib>> }
    class ThreadingMixIn { <<stdlib>> }
    class MySQLConnection { <<stdlib>> }
    class dict { <<builtin>> }

    %% ===== Inheritance =====
    dict <|-- _Section
    Thread <|-- AbstractThread
    AbstractThread <|-- MonitorManager
    AbstractThread <|-- CheckThread
    Check <|-- NoCheck
    Check <|-- IcmpCheck
    Check <|-- TcpCheck
    Check <|-- TlsCheck
    Check <|-- HttpCheck
    Check <|-- ExecCheck
    RoutingPolicy <|-- RoundRobin
    RoutingPolicy <|-- WeightedRandom
    RoutingPolicy <|-- StickyHash
    Thread <|-- HTTPServerManager
    ThreadingMixIn <|-- _ThreadingHTTPServer
    HTTPServer <|-- _ThreadingHTTPServer
    SimpleHTTPRequestHandler <|-- HTTPRequestHandler
    HTTPRequestHandler <|-- PowerDNSRequestHandler
    HTTPRequestHandler <|-- AdminRequestHandler
    PowerDNSDatabaseMixIn <|-- MySQLDatabase
    W2UIDatabaseMixIn <|-- MySQLDatabase
    MySQLConnection <|-- MySQLDatabase
    ServiceThread <|.. MonitorManager : satisfies
    ServiceThread <|.. HTTPServerManager : satisfies

    %% ===== Associations / composition =====
    PowerGSLB ..> Config : creates
    PowerGSLB ..> StatusRegistry : creates
    PowerGSLB ..> ViewRule : configure
    PowerGSLB ..> MonitorManager : creates
    PowerGSLB ..> HTTPServerManager : creates
    PowerGSLB ..> SystemService : creates
    Config ..> _Section : builds
    SystemService o--> "*" ServiceThread : supervises
    MonitorManager o--> "*" CheckThread : manages
    MonitorManager --> StatusRegistry
    MonitorManager ..> Check : create
    CheckThread --> Check
    CheckThread --> StatusWriter
    StatusRegistry ..> StatusWriter : creates
    StatusWriter --> StatusRegistry
    HTTPServerManager o--> _ThreadingHTTPServer : owns
    HTTPServerManager --> StatusRegistry
    HTTPServerManager ..> HTTPRequestHandler : instantiates per request
    HTTPRequestHandler --> MySQLDatabase : per-connection
    HTTPRequestHandler --> StatusRegistry
    AdminRequestHandler ..> MonitorManager : build_check (validate)
    AdminRequestHandler ..> RoutingPolicy : resolve (validate)
    AdminRequestHandler ..> ViewRule : resolve (validate)
    AdminRequestHandler ..> queryparser : parse_query
    queryparser ..> QueryParserError : raises
    W2UIDatabaseMixIn ..> password : hash / verify
    PowerDNSRequestHandler ..> RoutingPolicy : resolve
    PowerDNSRequestHandler ..> ViewRule : resolve
    PowerDNSRequestHandler ..> ClientContext : builds
    ViewRule o--> GeoIPReader : classvar
    ViewRule ..> ClientContext : reads / fills
    GeoIPReader ..> ClientGeo : returns
    RoutingPolicy ..> ClientContext : reads
```

</details>

---

## Quick start with the published Docker image

The fastest way to try PowerGSLB is the all-in-one image, which bundles PowerGSLB, PowerDNS Authoritative Server,
MariaDB, and systemd on a single RHEL UBI 10 base.

The run below is volume-less and disposable: each `docker run` starts from a clean, freshly-initialized database, and
removing the container discards everything. That is the right mode for a demo and tests. For any data that must outlive
the container, see [Persisting data](#persisting-data) below.

```shell
docker pull docker.io/acudovs/powergslb:2.2.0

docker run -it --privileged \
    --name powergslb --hostname powergslb \
    --tmpfs /run --tmpfs /tmp \
    docker.io/acudovs/powergslb:2.2.0
```

Find the container IP address and use it to reach the services:

```shell
CONTAINER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' powergslb)
```

Smoke-test DNS once the container is up:

```shell
dig @${CONTAINER_IP} example.com SOA
dig @${CONTAINER_IP} example.com A
dig @${CONTAINER_IP} example.com AAAA
dig @${CONTAINER_IP} example.com ANY
```

Then open the admin interface at `https://${CONTAINER_IP}/admin/`. Each container generates its own self-signed
certificate on first start, so the browser shows a security warning; proceed past it to reach the UI.

* Default username: `admin`
* Default password: `admin`

Change the default password after first login. Edit the `admin` user in the admin UI under the "Users" section.

Manage and stop the container:

```shell
docker exec -it powergslb bash
docker stop powergslb
```

To reach the services on the host instead of the container IP, publish the ports with
`-p 53:53/tcp -p 53:53/udp -p 443:443/tcp`. Note that these may conflict with a DNS resolver or HTTPS service already
listening on the host, so connecting to the container IP is usually simpler.

---

## Persisting data

The image ships an empty datadir and initializes the database on first start, so without a volume every run begins from
scratch. Any deployment whose data must outlive the container must mount a **named** volume at `/var/lib/mysql`:

```shell
docker volume create powergslb-db

docker run -it --privileged \
    --name powergslb --hostname powergslb \
    --tmpfs /run --tmpfs /tmp \
    -v powergslb-db:/var/lib/mysql \
    docker.io/acudovs/powergslb:2.2.0
```

First boot initializes the database inside the volume; later runs detect the existing data and reuse it untouched. A
bind mount (`-v "$PWD/db:/var/lib/mysql"`) or a Kubernetes PVC works the same way.

---

## Upgrading

Upgrading between PowerGSLB versions is a container swap; the volume is the only state carried across. Stop and remove
the old container, pull (or rebuild) the new image, and run it with the **same** named volume:

```shell
docker stop powergslb && docker rm powergslb
docker pull docker.io/acudovs/powergslb:"$NEW_VERSION"

docker run -it --privileged \
    --name powergslb --hostname powergslb \
    --tmpfs /run --tmpfs /tmp \
    -v powergslb-db:/var/lib/mysql \
    docker.io/acudovs/powergslb:"$NEW_VERSION"
```

---

## Building the Docker image

Build the image from a checkout of the repository instead of pulling it:

```shell
VERSION=$(PYTHONPATH=src python3 -c "from powergslb.version import VERSION; print(VERSION)")

docker build -f docker/Dockerfile --force-rm --no-cache -t powergslb:"$VERSION" .

docker run -it --privileged --name powergslb --hostname powergslb \
    --tmpfs /run --tmpfs /tmp \
    powergslb:"$VERSION"
```

---

## Manual setup

The Docker image is the recommended way to run PowerGSLB. To install the Python package directly - for development, or
to integrate with an existing PowerDNS and MariaDB - build a wheel and install it into a virtual environment.

Create a virtual environment (activation is required each time before use):

```shell
python3 -m venv --copies --system-site-packages --upgrade-deps .venv
source .venv/bin/activate
```

Install the build requirements and build the wheel:

```shell
pip install -r requirements-build.txt
pip wheel --wheel-dir dist --no-build-isolation --no-deps --verbose .
```

Install the built wheel:

```shell
pip install --force-reinstall --upgrade dist/powergslb-*-py3-none-any.whl
```

Run the service against a configuration file (`-c` / `--config` is required):

```shell
powergslb -c /etc/powergslb/powergslb.toml
```

The service also needs a MariaDB database with the schema and seed data loaded (`database/scheme.sql` and
`database/data.sql`), and a PowerDNS Remote Backend pointed at the DNS interface. See the files under `docker/rootfs/`
for reference configuration (`powergslb.toml` and `pdns.conf.powergslb`).

---

## Configuration

PowerGSLB is configured from a single TOML file, passed with `-c` / `--config`. The default file ships at
[docker/rootfs/etc/powergslb/powergslb.toml](docker/rootfs/etc/powergslb/powergslb.toml) and is deployed to
`/etc/powergslb/powergslb.toml` in the Docker image. Values are natively typed: ports and timeouts are integers, `ssl`
is a boolean, and the rest are strings.

| section      | purpose                         | key options                                                   |
|--------------|---------------------------------|---------------------------------------------------------------|
| `[database]` | MySQL / MariaDB connection      | `database`, `user`, `password`, `host`, `port`, `unix_socket` |
| `[logging]`  | Python logging                  | `format`, `level`                                             |
| `[monitor]`  | health-check engine             | `update_interval` (seconds), `icmp_privileged` (bool)         |
| `[server]`   | DNS interface (Remote Backend)  | `address`, `port`, `keep_alive_timeout`                       |
| `[admin]`    | admin interface (web UI + API)  | `address`, `port`, `ssl`, `cert`, `key`, `ciphers`, `root`    |
| `[geoip]`    | geo routing for [views](#views) | `database` (path to a GeoIP database)                         |

The `[database]` is passed straight to `mysql.connector` as connect kwargs. When `unix_socket` is set it takes
precedence over `host` / `port`.

The `[admin]` certificate is self-signed, generated once on first container start (the `powergslb-certgen` oneshot
unit writes `/etc/powergslb/powergslb.pem` only if it is missing) so each deployment gets its own unique cert. Replace
`cert` with your own PEM for production - `cert` may bundle the private key, or point `key` at a separate key file.
Both `[server]` and `[admin]` accept `keep_alive_timeout`, the HTTP keep-alive idle timeout in seconds.

The `[geoip]` section `database` is the path to a MaxMind DB (MMDB) file. The Docker image bundles the
[DB-IP IP-to-Country Lite](https://db-ip.com/db/download/ip-to-country-lite) at
`/usr/share/powergslb/dbip-country-lite.mmdb`; point `database` at a
[MaxMind GeoLite2 / GeoIP2](https://www.maxmind.com/) file to swap it.

### Environment overrides

Every option can be overridden by an environment variable named `POWERGSLB_<SECTION>_<OPTION>` (uppercased), coerced to
the configured value's type. This is how the Docker image is tuned without editing the file. Examples across sections:

```shell
POWERGSLB_DATABASE_HOST=192.168.1.20                  # connect to a remote database
POWERGSLB_DATABASE_PORT=3306
POWERGSLB_DATABASE_UNIX_SOCKET=                       # empty: use host/port over TCP instead of the socket
POWERGSLB_LOGGING_LEVEL=INFO
POWERGSLB_SERVER_ADDRESS=0.0.0.0                      # expose the DNS backend beyond loopback
POWERGSLB_ADMIN_PORT=8443
POWERGSLB_GEOIP_DATABASE=/data/GeoLite2-Country.mmdb  # use a MaxMind file instead of the bundled DB-IP Lite
```

The `[monitor]` section tunes the health-check engine as a whole - `update_interval` is how often it re-reads the
monitor configuration from the database, and `icmp_privileged` selects the raw vs. datagram ICMP socket:

```shell
POWERGSLB_MONITOR_UPDATE_INTERVAL=2       # pick up monitor changes faster (handy for testing)
POWERGSLB_MONITOR_ICMP_PRIVILEGED=false   # use an unprivileged ICMP datagram socket
```

Individual health checks are not part of this file: each monitor is a row of JSON in the database, edited in the admin
UI. See [Health checks](#health-checks) for the per-check parameters.

---

## Database

The DNS GSLB configuration lives in a MySQL 8 / MariaDB 10.5+ database. The schema uses a two-level model: an *rrset*
is one `(domain, name, type)` and owns its `ttl` and `routing` policy; a *record* is one answer inside it (`content`
plus the `monitor`, `view`, `weight` and `disabled` flag). Record names are stored relative to the zone (`@` for the
apex, otherwise the labels left of the domain), so in the admin grid the `Domain` column is authoritative and `Name`
is relative.

DNS invariants (CNAME exclusivity, SOA cardinality, rrset garbage collection) are enforced in the database itself via
CHECK constraints and triggers, so both the web UI and handwritten SQL are covered.

The schema, seed data, entity-relationship diagram, table reference, and the rationale behind the design are documented
in [database/README.md](database/README.md).

---

## Web administration interface

**Status**

![](https://raw.githubusercontent.com/acudovs/powergslb/refs/heads/master/images/web-status.png?raw=true)

**Advanced search**

![](https://raw.githubusercontent.com/acudovs/powergslb/refs/heads/master/images/web-records-search.png?raw=true)

**Add new record**

![](https://raw.githubusercontent.com/acudovs/powergslb/refs/heads/master/images/web-records-add.png?raw=true)

**Monitors**

![](https://raw.githubusercontent.com/acudovs/powergslb/refs/heads/master/images/web-monitors.png?raw=true)

**Views**

![](https://raw.githubusercontent.com/acudovs/powergslb/refs/heads/master/images/web-views.png?raw=true)

[More images](images)

---

## Record selection

For each query PowerGSLB starts from every enabled record at the requested `(name, type)` and runs a pipeline before
answering: the **view** filter (client match), then the **health** filter (drop down records), then the rrset's
**[routing policy](#routing-policies)**, which reads each record's `weight` and chooses the answers. The client IP used
for the view filter and for sticky routing is read from the `X-Remotebackend-Real-Remote` header PowerDNS sends (the
real resolver address), not the PowerDNS host.

If the view filter leaves no candidate for a query type, that type is answered with nothing. If the health filter would
empty a non-empty in-view set (every record is down), the down records are kept instead - "all down = all up" - so a
name never goes empty during a full outage; the policy then picks among them as a last resort.

### Views

A view maps clients to records, so one name can resolve differently per client. Each view holds a space-separated
`rule`, and a record references exactly one view; a record is a candidate only when the client matches the rule. The
seed data ships a `Public` view (`0.0.0.0/0 ::/0`, matching every client), a `Private` view (the RFC 1918 ranges),
and a geo `Europe` view (`country:DE country:FR continent:EU`).

A rule is a space-separated list or CIDR and geo tokens - the client matches when it satisfies any one of them:

* **CIDR** (IPv4 or IPv6): `10.0.0.0/8`, `2001:db8::/32` - matches when the client IP falls inside the range.
* `country:<ISO>` - a two-letter [ISO 3166-1 alpha-2](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2) country
  code, e.g. `country:DE`.
* `continent:<CODE>` - a two-letter continent code (`AF`, `AN`, `AS`, `EU`, `NA`, `OC`, `SA`), e.g. `continent:EU`.

Geo tokens are case-insensitive and may be mixed freely with CIDRs, e.g. `10.0.0.0/8 country:DE continent:EU`. The
client's country and continent are resolved from the [`[geoip]`](#configuration) database at most once per query, and
only when the CIDR tokens miss (the CIDR check short-circuits first). Each view rule is compiled once into a cached
object and reused, so repeated lookups do no re-parsing. When no database is loaded the geo tokens never match.

### Weight (priority)

Every routing policy reads the single per-record `weight`, but interprets it per its own rules (see
[Routing policies](#routing-policies)). Under the default `round-robin` and under `sticky-hash`, `weight` is a
**tier**: only the **highest-weight group** of live records is answered; equal-weight records all serve and load-share,
while lower-weight records stay on standby until every higher-weight record at the name is down. Under
`weighted-random`, `weight` is a **proportion** of the traffic instead.

The tier behavior enables a **blue-green deployment**: run the new servers alongside the old ones at a lower weight,
then raise their weight above the current group to cut all traffic over at once. The old servers fall to standby but
keep serving, so rolling back is just lowering the weight again. It also gives **backup-only** records: put the backups
in a lower weight tier under the same policy and they serve only once the whole primary tier is down.

### Routing policies

A routing policy is a named, reusable, JSON-configured object that an rrset references (like a monitor). It decides
*which* records to answer from the live, in-view candidates - never their order, since PowerDNS and recursors re-sort
RRsets. The seed data ships one of each type; manage them in the "Routings" sidebar section. Every rrset
references exactly one policy.

| policy            | weight | returns                                           | parameters (default)                                     |
|-------------------|--------|---------------------------------------------------|----------------------------------------------------------|
| `round-robin`     | tier   | up to `max_answers` from the highest tier         | `max_answers` (`8`)                                      |
| `weighted-random` | weight | up to `max_answers` by weighted random            | `max_answers` (`1`)                                      |
| `sticky-hash`     | tier   | up to `max_answers` from the highest tier, sticky | `max_answers` (`1`), `ipv4_mask`/`ipv6_mask` (`24`/`64`) |

* **round-robin** (default) answers the highest-weight live tier. A tier of `max_answers` or fewer is returned whole;
  a larger tier is randomly subsampled to `max_answers` to bound UDP fragmentation / `TC=1` truncation. Example:
  `{"type": "round-robin"}` or `{"type": "round-robin", "max_answers": 4}`.
* **weighted-random** answers up to `max_answers` records (default `1`), sampled weighted-random by `weight` without
  replacement over all live records. With the default single answer each query picks one record by weight, so the
  proportional split is exact across queries; `max_answers` above 1 returns several weighted records per answer that
  resolvers may reorder, so the split then holds only statistically. An all-zero-weight set samples evenly. Example:
  `{"type": "weighted-random"}` or `{"type": "weighted-random", "max_answers": 3}`.
* **sticky-hash** answers up to `max_answers` records (default `1`) from the highest live tier, pinned per client
  network via rendezvous (HRW) hashing: the client is masked to `ipv4_mask` / `ipv6_mask` and records are ranked by a
  salt-free hash of `(network, content)`, returning the top `max_answers`, so a health flap or record change remaps
  only ~`max_answers`/N clients (~1/N at the default `max_answers`). Stickiness is stable per client network **given
  the same live set and masks**. Example:
  `{"type": "sticky-hash"}` or `{"type": "sticky-hash", "max_answers": 2, "ipv4_mask": 16}`.

Liveness is decided by the [health checks](#health-checks) below. Across all policies, when every record at a name is
down the set is reactivated ("all down = all up") so the name still resolves; `round-robin` / `sticky-hash` then serve
the highest-weight tier as a last resort, and `weighted-random` falls back to its split.

### Disabled records

A record can be administratively disabled in the admin UI. A disabled record is excluded from every DNS answer
regardless of health, view, or weight - handy for draining an endpoint for maintenance without deleting its
configuration.

---

## Health checks

Health checks are configured in the "Monitors" sidebar section in JSON format.

Supported check types:

| type | description                 |
|------|-----------------------------|
| none | no check (always healthy)   |
| exec | arbitrary command execution |
| icmp | ICMP ping                   |
| http | HTTP request                |
| tcp  | TCP connect                 |
| tls  | TLS connect                 |

### General parameters

Parameters shared by all check types. Only `type` is required; the timing parameters are optional and fall back to
their defaults, so a monitor JSON may omit them.

| parameter | description                                  | default |
|-----------|----------------------------------------------|---------|
| type      | check type                                   |         |
| interval  | seconds between checks                       | `3`     |
| timeout   | per-run check timeout in seconds             | `1`     |
| fall      | number of failed checks to disable record    | `3`     |
| rise      | number of successful checks to enable record | `5`     |

The `none` type takes no parameters (`{"type": "none"}`); it is the "No check" monitor and is never run.

The token `${content}` in any string value is replaced with the record's content (typically its IP address), so one
monitor can serve many records. Every other character - including `%`, `$`, `{` and `}` - is treated literally and
needs no escaping.

A check does not have to target the record's own content. Because the target is whatever you put in the monitor JSON,
you can omit `${content}` and hard-code any IP, URL, or command, so a record's liveness is gated on a separate endpoint
or a script. This is useful when a record should serve only while some dependency is reachable - an origin behind a CDN
record, an upstream gateway, a database, or any external API:

```json
{"type": "http", "url": "https://origin.example.com/health"}
```

### Exec parameters

| parameter      | description                                                        | default |
|----------------|--------------------------------------------------------------------|---------|
| type           | exec                                                               |         |
| args           | command to execute and arguments                                   |         |
| expected_code  | exit code that counts as healthy                                   | `0`     |
| output_match   | regex against the first 64 KiB of output; `""` skips the scan      | `""`    |
| redirect_error | merge the command's stderr into stdout so `output_match` sees both | `true`  |

Example:

```json
{"type": "exec", "args": ["/etc/powergslb/powergslb-check", "${content}"]}
```

The whole run is bounded by `timeout`; on timeout the process is killed and the check fails. Only the first 64 KiB of
output is kept for `output_match`; any excess is drained so a chatty command can still exit.

### ICMP parameters

| parameter | description         |
|-----------|---------------------|
| type      | icmp                |
| ip        | endpoint IP address |

Example:

```json
{"type": "icmp", "ip": "${content}"}
```

ICMP checks open a raw ICMP socket and therefore need `CAP_NET_RAW` or root. The shipped container satisfies this:
the service runs as root and `powergslb.service` keeps `CAP_NET_RAW` in `CapabilityBoundingSet`. To run unprivileged,
set `icmp_privileged = false` in the `[monitor]` config section: this uses an ICMP datagram socket, but only works when
the service's GID is inside the kernel `net.ipv4.ping_group_range` range:

```bash
sysctl -w net.ipv4.ping_group_range="0 2147483647"
```

### HTTP parameters

| parameter       | description                                                              | default     |
|-----------------|--------------------------------------------------------------------------|-------------|
| type            | http                                                                     |             |
| url             | endpoint URL                                                             |             |
| method          | request method, `GET` or `HEAD`                                          | `GET`       |
| expected_status | comma-separated codes and inclusive ranges, e.g. `"101,200-204,300-308"` | `"200-399"` |
| body_match      | regex against the first 64 KiB of body; `GET` only; `""` skips the scan  | `""`        |
| tls_verify      | verify the server TLS certificate                                        | `true`      |
| host            | override the HTTP `Host` header; TCP destination unchanged; `""` off     | `""`        |

Redirects are never followed: a `3xx` is evaluated on its own status (accepted by the default success range).

Example:

```json
{"type": "http", "url": "http://${content}/health"}
```

Example with optional parameters - require an exact `200` carrying `"ok"` in the body, over self-signed HTTPS, and
override two timing defaults:

```json
{
  "type": "http",
  "url": "https://${content}/health",
  "method": "GET",
  "expected_status": "200",
  "body_match": "\"status\":\\s*\"ok\"",
  "tls_verify": false,
  "host": "health.example.com",
  "interval": 5,
  "fall": 2
}
```

### TCP parameters

| parameter | description          |
|-----------|----------------------|
| type      | tcp                  |
| ip        | endpoint IP address  |
| port      | endpoint port number |

Example:

```json
{"type": "tcp", "ip": "${content}", "port": 80}
```

The check opens a TCP connection to `ip:port` and passes as soon as the handshake completes; it sends no data and
reads no response. Connection setup is bounded by `timeout`; a refused connection or a timeout fails the check.

### TLS parameters

| parameter  | description                                                            | default |
|------------|------------------------------------------------------------------------|---------|
| type       | tls                                                                    |         |
| ip         | endpoint IP address                                                    |         |
| port       | endpoint port number                                                   |         |
| tls_verify | verify the server TLS certificate                                      | `true`  |
| host       | SNI server name and verified certificate name; `""` falls back to `ip` | `""`    |

Example:

```json
{"type": "tls", "ip": "${content}", "port": 443}
```

The check opens a TCP connection to `ip:port` and completes the TLS handshake. Connection setup and the handshake are
bounded by `timeout`. With `tls_verify` (the default `true`), an untrusted chain, an expired certificate, or a
hostname mismatch fails the check; set `tls_verify` to `false` to require only that the handshake completes. Unlike
`tcp`, which stops at the TCP handshake, `tls` confirms the endpoint actually serves TLS - use it for non-HTTP TLS
services (SMTPS, IMAPS, LDAPS, etc.) that the `http` check cannot handle.

### Trust custom CA certificates

With `tls_verify` (used by the `http` and `tls` checks), each check validates the endpoint chain against the image's
system trust store. To check endpoints served by a private or internal CA, add that CA so the checks trust it:

1. Copy the CA certificate (PEM or DER, named `.crt` or `.pem`) into `docker/rootfs/etc/pki/ca-trust/source/anchors/`.
2. [Rebuild the image](#building-the-docker-image).

The build runs `update-ca-trust`, folding the certificate into the system trust store that OpenSSL and Python's `ssl`
read, so `tls_verify` succeeds.

---

## API

PowerGSLB exposes two HTTP interfaces, both returning JSON:

* **DNS backend** - the PowerDNS Remote Backend protocol, read-only, plain HTTP (default `127.0.0.1:8080`). It binds
  loopback by default, so reach it from inside the container or set `POWERGSLB_SERVER_ADDRESS=0.0.0.0` to expose it.
  `GET /dns/lookup/<qname>./<qtype>` returns the filtered answers and `GET /dns/getAllDomains` returns the zone list.
* **Admin API** - the w2ui CRUD endpoint at `POST /admin/w2ui` over HTTPS (default `:443`), behind HTTP Basic Auth.
  Parameters are form-encoded (also accepted on the GET query string); records are addressed by `cmd` and a `data`
  table, and `monitor` and `view` are matched by name, not id.

  The same commands apply to every table - `data` is one of `domains`, `monitors`, `views`, `records`, `types`,
  `users`, `status`:
    * `get-records` - list a table; supports `search`, `sort`, and `limit`/`offset` paging.
    * `get-record` (`recid=<id>`) - fetch one row by id.
    * `get-items` (`field=<column>`) - list the distinct values of one column.
    * `save-record` (`recid=0` to insert, `recid=<id>` to update) - write one row from `record[...]` fields.
    * `delete-records` (`selected[0]=<id>`) - delete rows by id.

  An update re-sends the whole row, so editing one field (a record's weight, say) is a read-modify-write:
  `get-record`, change the field, `save-record` with the unchanged fields preserved.

### curl

```shell
# DNS backend (inside the container; loopback by default)
curl 'http://127.0.0.1:8080/dns/lookup/example.com./A'
curl 'http://127.0.0.1:8080/dns/getAllDomains'

# Admin API: list records (-k accepts the self-signed certificate)
curl -sk -u admin:admin https://powergslb/admin/w2ui -d cmd=get-records -d data=records

# Admin API: fetch one record by id (the id is the recid field from get-records)
curl -sk -u admin:admin https://powergslb/admin/w2ui -d cmd=get-record -d data=records -d recid=133

# Admin API: create an A record (omitted fields - disabled, weight - default to 0)
curl -sk -u admin:admin https://powergslb/admin/w2ui \
    -d cmd=save-record -d data=records -d recid=0 \
    -d 'record[domain]=example.com' \
    -d 'record[name]=app' \
    -d 'record[name_type]=A' \
    -d 'record[ttl]=60' \
    -d 'record[content]=192.0.2.10' \
    -d 'record[monitor]=No check' \
    -d 'record[view]=Public' \
    -d 'record[policy]=Round robin'

# Admin API: change a record's weight (recid=133 updates in place; re-send the row's other fields unchanged)
curl -sk -u admin:admin https://powergslb/admin/w2ui \
    -d cmd=save-record -d data=records -d recid=133 \
    -d 'record[domain]=example.com' \
    -d 'record[name]=app' \
    -d 'record[name_type]=A' \
    -d 'record[ttl]=60' \
    -d 'record[content]=192.0.2.10' \
    -d 'record[monitor]=No check' \
    -d 'record[view]=Public' \
    -d 'record[policy]=Round robin' \
    -d 'record[weight]=10'

# Admin API: delete a record by id
curl -sk -u admin:admin https://powergslb/admin/w2ui -d cmd=delete-records -d data=records -d 'selected[0]=133'

# Admin API: list / add domains
curl -sk -u admin:admin https://powergslb/admin/w2ui -d cmd=get-records -d data=domains
curl -sk -u admin:admin https://powergslb/admin/w2ui \
    -d cmd=save-record -d data=domains -d recid=0 \
    -d 'record[domain]=example.net'

# Admin API: list monitors / add a TCP check (monitor_json is the check definition; ${content} expands to the record)
curl -sk -u admin:admin https://powergslb/admin/w2ui -d cmd=get-records -d data=monitors
curl -sk -u admin:admin https://powergslb/admin/w2ui \
    -d cmd=save-record -d data=monitors -d recid=0 \
    -d 'record[monitor]=TCP 443' \
    -d 'record[monitor_json]={"type": "tcp", "ip": "${content}", "port": 443}'

# Admin API: list views / add a view (rule is a space-separated list or CIDR and geo tokens)
curl -sk -u admin:admin https://powergslb/admin/w2ui -d cmd=get-records -d data=views
curl -sk -u admin:admin https://powergslb/admin/w2ui \
    -d cmd=save-record -d data=views -d recid=0 \
    -d 'record[view]=Internal' \
    -d 'record[rule]=10.0.0.0/8 192.168.0.0/16'

# Admin API: add a geo view
curl -sk -u admin:admin https://powergslb/admin/w2ui \
    -d cmd=save-record -d data=views -d recid=0 \
    -d 'record[view]=Europe' \
    -d 'record[rule]=country:DE country:FR continent:EU'
```

The values here need no URL-encoding (none contain `&`, `+`, `%`, or `=`), so plain `-d` is enough; reach for
`--data-urlencode` if a field ever carries one of those characters.

### Python

The integration suite ships ready-made `DNSClient` and `W2UIClient` wrappers in
[tests/integration/conftest.py](tests/integration/conftest.py); reuse them as a reference client. A minimal
`requests`-based equivalent:

```python
import requests

# DNS backend (loopback by default)
requests.get("http://127.0.0.1:8080/dns/lookup/example.com./A", timeout=10).json()

# Admin API
ADMIN = "https://powergslb/admin/w2ui"
AUTH = ("admin", "admin")

def w2ui(cmd, data, **params):
    params.update(cmd=cmd, data=data)
    # verify=False: the demo image ships a self-signed certificate
    return requests.get(ADMIN, params=params, auth=AUTH, verify=False, timeout=15).json()

def save(data, recid, fields):
    return w2ui("save-record", data, recid=recid, **{f"record[{k}]": v for k, v in fields.items()})

# Records: list, create, delete
records = w2ui("get-records", "records")["records"]
save("records", 0, {"domain": "example.com", "name": "app", "name_type": "A", "ttl": 60,
                    "content": "192.0.2.10", "monitor": "No check", "view": "Public", "policy": "Round robin"})
w2ui("delete-records", "records", **{"selected[0]": 133})

# Change a record's weight: read-modify-write (an update re-sends the whole row)
record = w2ui("get-record", "records", recid=133)["record"]
record["weight"] = 10
save("records", record["recid"], record)

# Domains
save("domains", 0, {"domain": "example.net"})

# Monitors: monitor_json is the check definition; ${content} expands to the record content
save("monitors", 0, {"monitor": "TCP 443", "monitor_json": '{"type": "tcp", "ip": "${content}", "port": 443}'})

# Views: rule is a space-separated list or CIDR and geo tokens
save("views", 0, {"view": "Internal", "rule": "10.0.0.0/8 192.168.0.0/16"})
save("views", 0, {"view": "Europe", "rule": "country:DE country:FR continent:EU"})
```

---

## Tests

The repository ships with three checks:

* **Linting** - `pylint` and `mypy` over `src` and `tests`.
* **Unit tests** - in-process tests under `tests/unit/`, run under coverage; no container required.
* **Integration tests** - black-box tests under `tests/integration/` against a freshly built Docker container.

See [tests/README.md](tests/README.md) for the layout, the exact commands, and how to point the suite at a non-default
host or database.

---

## License

PowerGSLB is released under the MIT License. See [LICENSE](LICENSE) for details.

The Docker image bundles the [IP Geolocation by DB-IP](https://db-ip.com/) database,
licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
