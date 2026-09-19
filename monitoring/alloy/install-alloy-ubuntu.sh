#!/usr/bin/env bash
# Install Grafana Alloy on an Ubuntu host (rasp5, rk1-rk4) and ship its logs to
# Loki on 192.168.1.98 - same config as the Rocky hosts (MONITORING_SETUP.md §4.12).
#
# Run from rasp5, in ~/monitoring/alloy:   ./install-alloy-ubuntu.sh rk1 [rk2 ...]
# (Or on the target itself:                ./install-alloy-ubuntu.sh --local)
# Loki on .98 must already allow the host's IP on :3100 (firewalld rich rule, §6).
set -euo pipefail
V=1.19.2
DIR=$(cd "$(dirname "$0")" && pwd)

install_local() {
  local arch; arch=$(dpkg --print-architecture)          # amd64 | arm64
  local tmp; tmp=$(mktemp -d)
  cd "$tmp"
  curl -sSLO "https://github.com/grafana/alloy/releases/download/v${V}/alloy-${V}-1.${arch}.deb"
  curl -sSLO "https://github.com/grafana/alloy/releases/download/v${V}/SHA256SUMS"
  grep " alloy-${V}-1.${arch}.deb$" SHA256SUMS | sha256sum -c -
  sudo apt-get install -y -qq "./alloy-${V}-1.${arch}.deb" >/dev/null
  sudo mkdir -p /etc/systemd/system/alloy.service.d
  sudo install -m 0644 "$1/alloy-root.conf" /etc/systemd/system/alloy.service.d/10-lab.conf
  [ -f /etc/alloy/config.alloy ] && sudo mv /etc/alloy/config.alloy /etc/alloy/config.alloy.pkg-default
  sudo install -m 0644 "$1/common.alloy" /etc/alloy/common.alloy
  # optional sources, only where the thing exists
  if systemctl is-active -q docker; then sudo install -m 0644 "$1/docker.alloy" /etc/alloy/docker.alloy; fi
  if [ -d /var/log/pods ]; then sudo install -m 0644 "$1/k3s-pods.alloy" /etc/alloy/k3s-pods.alloy; fi
  sudo sed -i 's|^CONFIG_FILE=.*|CONFIG_FILE="/etc/alloy"|' /etc/default/alloy
  sudo systemctl daemon-reload
  sudo systemctl enable -q alloy
  sudo systemctl restart alloy
  sleep 5
  echo "$(hostname): alloy $(systemctl is-active alloy); configs: $(sudo sh -c "cd /etc/alloy && ls *.alloy" | tr "\n" " ")"
  rm -rf "$tmp"
}

if [ "${1:-}" = "--local" ]; then
  install_local "$DIR"
  exit
fi
[ $# -ge 1 ] || { echo "usage: $0 <host>... | --local" >&2; exit 1; }
for h in "$@"; do
  echo "===== $h"
  ssh "$h" 'rm -rf ~/alloy-inst && mkdir ~/alloy-inst'
  scp -q "$DIR"/{common.alloy,docker.alloy,k3s-pods.alloy,alloy-root.conf,install-alloy-ubuntu.sh} "$h":alloy-inst/
  ssh "$h" 'bash ~/alloy-inst/install-alloy-ubuntu.sh --local && rm -rf ~/alloy-inst'
done
