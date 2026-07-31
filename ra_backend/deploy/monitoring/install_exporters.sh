#!/usr/bin/env bash
# RADONAIX — install the Prometheus exporters a server needs, by role.
#
#   sudo ./install_exporters.sh <role> [--bind-ip <ip>] [--prom-host <ip>]
#
# Roles → exporters (see docs/MONITORING.md §9):
#   app            node_exporter + postgres_exporter   (Airflow metadata DB)
#   db-postgres    node_exporter + postgres_exporter
#   db-clickhouse  node_exporter + enable ClickHouse's native /metrics on :9363
#   report         node_exporter + postgres_exporter   (application DB)
#   archive        node_exporter
#
#   --bind-ip     address the exporters listen on (default: the host's primary IP).
#                 Never bind 0.0.0.0 on a shared network.
#   --prom-host   the Prometheus server allowed to scrape (default: 10.200.37.142).
#
# Idempotent: safe to re-run. Handles the SELinux `restorecon` step that otherwise
# fails systemd with 203/EXEC, and opens the scrape ports to the Prometheus host only.
set -euo pipefail

NODE_EXPORTER_VERSION="1.8.2"
PG_EXPORTER_VERSION="0.15.0"

ROLE="${1:-}"
shift || true
BIND_IP=""
PROM_HOST="10.200.37.142"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bind-ip)   BIND_IP="$2"; shift 2 ;;
    --prom-host) PROM_HOST="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$ROLE" in
  app|db-postgres|db-clickhouse|report|archive) ;;
  *) echo "usage: $0 <app|db-postgres|db-clickhouse|report|archive> [--bind-ip IP] [--prom-host IP]" >&2; exit 2 ;;
esac
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

[[ -n "$BIND_IP" ]] || BIND_IP="$(hostname -I | awk '{print $1}')"
echo "==> role=$ROLE  bind=$BIND_IP  prometheus=$PROM_HOST"

have_firewalld() { systemctl is-active --quiet firewalld 2>/dev/null; }

open_port() {  # open_port <port> — only to the Prometheus host
  local port="$1"
  have_firewalld || { echo "    firewalld inactive — open :$port to $PROM_HOST yourself"; return 0; }
  firewall-cmd --permanent --add-rich-rule="rule family='ipv4' source address='${PROM_HOST}/32' port port='${port}' protocol='tcp' accept" >/dev/null || true
  firewall-cmd --reload >/dev/null
  echo "    firewalld: :$port open to $PROM_HOST only"
}

fetch_binary() {  # fetch_binary <name> <version> <url>
  local name="$1" version="$2" url="$3"
  if [[ -x "/usr/local/bin/$name" ]]; then
    echo "    $name already installed"
  else
    local tmp; tmp="$(mktemp -d)"
    curl -fsSL "$url" | tar xz -C "$tmp" --strip-components=1
    install -m 0755 "$tmp/$name" "/usr/local/bin/$name"
    rm -rf "$tmp"
    echo "    $name $version installed"
  fi
  # SELinux: a binary that came from /tmp keeps tmp_t and systemd refuses to exec
  # it (status=203/EXEC). Relabel it. This is not optional on RHEL.
  command -v restorecon >/dev/null && restorecon -v "/usr/local/bin/$name" || true
  id -u "$name" >/dev/null 2>&1 || useradd -rs /sbin/nologin "$name"
}

write_unit() {  # write_unit <name> <ExecStart> [EnvironmentFile]
  local name="$1" exec_start="$2" env_file="${3:-}"
  {
    echo "[Unit]"
    echo "Description=Prometheus ${name}"
    echo "After=network-online.target"
    echo ""
    echo "[Service]"
    echo "User=${name}"
    [[ -n "$env_file" ]] && echo "EnvironmentFile=${env_file}"
    echo "ExecStart=${exec_start}"
    echo "Restart=always"
    echo "RestartSec=3"
    echo ""
    echo "[Install]"
    echo "WantedBy=multi-user.target"
  } > "/etc/systemd/system/${name}.service"
  systemctl daemon-reload
  systemctl enable --now "${name}" >/dev/null
  systemctl is-active --quiet "${name}" && echo "    ${name}: active" || {
    echo "    ${name}: FAILED — journalctl -u ${name} -n 30"; exit 1; }
}

install_node_exporter() {
  echo "--> node_exporter (:9100)"
  fetch_binary node_exporter "$NODE_EXPORTER_VERSION" \
    "https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz"
  write_unit node_exporter "/usr/local/bin/node_exporter --web.listen-address=${BIND_IP}:9100"
  open_port 9100
}

install_postgres_exporter() {
  echo "--> postgres_exporter (:9187)"
  fetch_binary postgres_exporter "$PG_EXPORTER_VERSION" \
    "https://github.com/prometheus-community/postgres_exporter/releases/download/v${PG_EXPORTER_VERSION}/postgres_exporter-${PG_EXPORTER_VERSION}.linux-amd64.tar.gz"
  local env_file=/etc/monitoring/postgres_exporter.env
  mkdir -p /etc/monitoring
  if [[ ! -f "$env_file" ]]; then
    cat > "$env_file" <<'ENV'
# Read-only monitoring role. Create it first:
#   CREATE USER radonaix_mon WITH PASSWORD '<pw>';
#   GRANT pg_monitor TO radonaix_mon;
DATA_SOURCE_NAME="postgresql://radonaix_mon:<pw>@127.0.0.1:5432/postgres?sslmode=disable"
ENV
    chmod 600 "$env_file"
    echo "    !! created $env_file with a PLACEHOLDER — set DATA_SOURCE_NAME, then:"
    echo "       systemctl restart postgres_exporter"
  fi
  write_unit postgres_exporter "/usr/local/bin/postgres_exporter --web.listen-address=${BIND_IP}:9187" "$env_file"
  open_port 9187
}

enable_clickhouse_metrics() {
  echo "--> ClickHouse native metrics (:9363)"
  local conf=/etc/clickhouse-server/config.d/prometheus.xml
  [[ -d /etc/clickhouse-server/config.d ]] || { echo "    ClickHouse config dir not found" >&2; exit 1; }
  cat > "$conf" <<XML
<clickhouse>
    <prometheus>
        <endpoint>/metrics</endpoint>
        <port>9363</port>
        <metrics>true</metrics>
        <events>true</events>
        <asynchronous_metrics>true</asynchronous_metrics>
        <errors>true</errors>
    </prometheus>
</clickhouse>
XML
  echo "    wrote $conf — restart ClickHouse to apply: systemctl restart clickhouse-server"
  open_port 9363
}

install_node_exporter
case "$ROLE" in
  app|db-postgres|report) install_postgres_exporter ;;
  db-clickhouse)          enable_clickhouse_metrics ;;
  archive)                : ;;
esac

echo ""
echo "==> done. Now append this server to the Prometheus targets on ${PROM_HOST}:"
echo "    deploy/prometheus/targets/nodes.json   (always)"
case "$ROLE" in
  app|db-postgres|report) echo "    deploy/prometheus/targets/postgres.json" ;;
  db-clickhouse)          echo "    deploy/prometheus/targets/clickhouse.json  (target ${BIND_IP}:9363)" ;;
esac
echo "    Prometheus reloads within 30s — no restart. See docs/MONITORING.md §9."
