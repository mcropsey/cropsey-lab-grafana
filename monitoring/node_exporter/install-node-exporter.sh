#!/usr/bin/env bash
# Install Prometheus node_exporter on a lab host and expose :9100 for the
# Prometheus server on 192.168.1.98 to scrape (MONITORING_SETUP.md).
#
# Run from rasp5, in ~/monitoring/node_exporter:  ./install-node-exporter.sh <host>...
# (Or on the target itself:                       ./install-node-exporter.sh --local)
#
# After install, add the host to the `node` job in
#   /opt/monitoring/prometheus/config/prometheus.yml  on .98 and reload:
#   curl -XPOST http://localhost:9090/-/reload
#
# Firewall: Rocky hosts must allow :9100 from .98 (firewalld rich rule / nft
# monitoring_acl). rasp5 (.85) has no firewall, so nothing to open there.
set -euo pipefail
V=1.12.1
DIR=$(cd "$(dirname "$0")" && pwd)
# systemd units reported by --collector.systemd. Override per host with UNITS=...
: "${UNITS:=(docker|containerd|node_exporter|alloy|glances|atop|k3s|cloudflared|podman|prometheus-podman-exporter|mcp-heartbeat|mcp-sweep|ai-sim|crapi-mcp|noname-mcp|noname-sensor)}"

install_local() {
  local goarch; case "$(uname -m)" in
    x86_64)  goarch=amd64 ;;
    aarch64) goarch=arm64 ;;
    *) echo "unsupported arch $(uname -m)" >&2; exit 1 ;;
  esac
  local tmp; tmp=$(mktemp -d); cd "$tmp"
  curl -sSLO "https://github.com/prometheus/node_exporter/releases/download/v${V}/node_exporter-${V}.linux-${goarch}.tar.gz"
  curl -sSLO "https://github.com/prometheus/node_exporter/releases/download/v${V}/sha256sums.txt"
  grep " node_exporter-${V}.linux-${goarch}.tar.gz$" sha256sums.txt | sha256sum -c -
  tar xzf "node_exporter-${V}.linux-${goarch}.tar.gz"
  sudo install -m 0755 -o root -g root "node_exporter-${V}.linux-${goarch}/node_exporter" /usr/local/bin/node_exporter
  id node_exporter >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin node_exporter
  sudo tee /etc/systemd/system/node_exporter.service >/dev/null <<UNIT
# /etc/systemd/system/node_exporter.service
[Unit]
Description=Prometheus node_exporter
Documentation=https://github.com/prometheus/node_exporter
Wants=network-online.target
After=network-online.target

[Service]
User=node_exporter
Group=node_exporter
Type=simple
ExecStart=/usr/local/bin/node_exporter \\
  --web.listen-address=:9100 \\
  --collector.systemd \\
  --collector.systemd.unit-include=${UNITS}\\.service \\
  --collector.filesystem.mount-points-exclude=^/(dev|proc|run|sys|var/lib/docker/.+|var/lib/containers/.+|var/lib/kubelet/.+|run/k3s/.+)(\$|/)
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now node_exporter
  sleep 2
  echo "$(hostname): node_exporter $(systemctl is-active node_exporter) $(/usr/local/bin/node_exporter --version 2>&1 | head -1)"
  rm -rf "$tmp"
}

if [ "${1:-}" = "--local" ]; then install_local; exit; fi
[ $# -ge 1 ] || { echo "usage: $0 <host>... | --local" >&2; exit 1; }
for h in "$@"; do
  echo "===== $h"
  scp -q "$DIR/install-node-exporter.sh" "$h":/tmp/install-node-exporter.sh
  ssh "$h" 'bash /tmp/install-node-exporter.sh --local && rm -f /tmp/install-node-exporter.sh'
done
