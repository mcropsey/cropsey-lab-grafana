# Lab Monitoring Setup: Prometheus + Grafana + Alertmanager + Loki

Installed and validated **2026-09-18** on the 5-host Rocky Linux 9.8 Hyper-V lab
(`192.168.1.98`–`.102`). You can follow this document by hand, without Claude Code.

**Final validated state:** all 15 Prometheus targets are `UP` (5 × node_exporter,
2 × k3s kubelet, 2 × cAdvisor, 3 × podman-exporter, plus Prometheus and Alertmanager
self-scrapes, plus Loki from 2026-09-19). Every panel query on the custom dashboards returns live data
through Grafana. No existing workload was restarted or reconfigured.

**Logs added 2026-09-19:** Loki 3.7.8 on .98 plus Grafana Alloy 1.19.2 on all 5 hosts. Logs from
every host (systemd journal including all Podman containers, Docker containers, k3s pods, auditd)
are in Grafana under **Drilldown → Logs**, **Explore → Loki**, the new **Logs** folder, and a
"Logs: errors & warnings" panel at the bottom of each host dashboard. Prometheus now shows
**15** targets (the new one is `loki`). Setup is in §3.7, §4.10, and §5.13–§5.18.

**Logs extended the same day** to **rasp5** (`.85`, Ubuntu 24.04 arm64: journal + Docker). **rk1–rk4**
(`.75`–`.78`) are prepared (Loki firewall already open for them, install script ready) but were
**offline** (no ARP reply) on 2026-09-19, so Alloy is **not yet installed** there; see §4.12.
`.103`–`.105` are deliberately **not** included.

**Log alerts added 2026-09-19:** 8 log-based alerts (6 from the Loki ruler, 2 in Prometheus on
error-rate series that Loki records) that go to the existing Alertmanager. See §3.8.

---

## 1. Host / role table

| Host | IP | Role (existing) | Monitoring added | Ports added |
|---|---|---|---|---|
| hv-rocky-linux-1 | 192.168.1.98 | client / traffic generator (`vnotes` Docker, mcp-heartbeat, mcp-sweep, cron) | **Prometheus, Grafana, Alertmanager, cAdvisor, Loki** (Docker Compose, `/opt/monitoring`), node_exporter, Alloy | 9090, 3000, 9093, 8081, 9100, 3100 (127.0.0.1: 9095, 12345) |
| hv-rocky-linux-2 | 192.168.1.99 | k3s v1.36.3 single node (nginx, juice-shop, vampi+sensor, ingress-nginx), cloudflared | node_exporter, Alloy; kubelet scraped remotely on existing :10250; RBAC `monitoring/prometheus-scraper` | 9100 (127.0.0.1: 12345) |
| hv-rocky-linux-3 | 192.168.1.100 | API gateway (kong, Podman) + CI (jenkins + dind, Docker) | node_exporter, cAdvisor (Docker), prometheus-podman-exporter (kong), Alloy | 9100, 8081, 9882 (127.0.0.1: 12345) |
| hv-rocky-linux-4 | 192.168.1.101 | **crAPI vulnerable-app security lab** (12 Podman containers) | node_exporter, prometheus-podman-exporter, Alloy | 9100, 9882 (127.0.0.1: 12345) |
| hv-rocky-linux-5 | 192.168.1.102 | MCP servers (ai-sim, crapi-mcp, noname-mcp: systemd) + vampi-mcp (Podman) | node_exporter, prometheus-podman-exporter, Alloy | 9100, 9882 (127.0.0.1: 12345) |

Component versions: Prometheus **v3.14.0**, Alertmanager **v0.34.1**, Grafana **13.2.2**,
node_exporter **1.12.1**, cAdvisor **v0.60.6** (`ghcr.io/google/cadvisor`),
prometheus-podman-exporter **1.21.2** (EPEL 9), Loki **3.7.8** (`grafana/loki`),
Grafana Alloy **1.19.2** (standalone RPM from GitHub releases; no dnf repo added).

URLs (LAN 192.168.1.0/24 only):
- Grafana: http://192.168.1.98:3000 (user `admin`; the password is in `/opt/monitoring/.env` on .98, `sudo cat` it)
- Prometheus: http://192.168.1.98:9090 (targets: `/targets`)
- Alertmanager: http://192.168.1.98:9093
- Logs: Grafana → **Drilldown → Logs**, or **Explore** with data source **Loki**, or dashboard **Logs / Logs (all hosts)**.
  Loki itself (:3100) is **not** reachable from the LAN, only from the log-shipping hosts (.99–.102, .85, .75–.78; see §6).
  If Drilldown ever says **"Log volume has not been configured"**, it's querying Prometheus instead of Loki:
  pick **Loki** in its *Data source* dropdown (the §5.19 provisioning makes Loki the default).

---

## 2. Port choices and why

Ports that were in use (inventory, re-verified with `ss -tlnp` on 2026-09-18):

| Host | Already in use |
|---|---|
| .98 | 22, 8000 (vnotes), 127.0.0.1:44321 (opencode), 127.0.0.1:631 |
| .99 | 22, 80, 443, 8181, 8443, 10254, 6443, 10250, NodePorts 30300/30453/30500 (iptables, no listener), 127.0.0.1: 6444, 10010, 10245-10249, 10256-10259, 20241, 631; udp 8472 |
| .100 | 22, 80 (kong→8000), 8001 (kong admin), 8080 (jenkins), 50000, 2376 (dind) |
| .101 | treated as reserved: 8443, 8888, 30080, 30443, 8080, 8989, 10001, 6060, 5500, 5002, 443, 8025, 1025, 3000 (only 8 of these are actually host-published, see §9) |
| .102 | 22, 5000 (vampi-mcp), 8009, 8011, 8012, 8013 |

| Exporter / service | Port | Hosts | Why this port |
|---|---|---|---|
| node_exporter | **9100** | all 5 | Upstream default; free on all 5. |
| cAdvisor | **8081** | .98, .100 | Free on **all 5** hosts, so the same port is used everywhere for consistency. Not 8000/8080 (vnotes, kong, jenkins, crAPI, searxng use those). |
| prometheus-podman-exporter | **9882** | .100, .101, .102 | Upstream default; in the 9xxx range, clear of every in-use port on .100 (80/8000/8001/8080/50000/2376), .101 (14 listed), and .102 (5000/8009/8011-8013). |
| Prometheus | 9090 | .98 | Default; free. |
| Alertmanager | 9093 | .98 | Default; free. Cluster gossip port 9094 **disabled** (`--cluster.listen-address=`), since this is a single instance. |
| Grafana | 3000 | .98 | Free on .98 (juice-shop uses 3000 only on .101). |
| Loki HTTP (push + query) | **3100** | .98 | Upstream default; free on .98 (re-checked 2026-09-19). Open only to the Alloy agents (.99–.102, rasp5 .85, rk1–rk4 .75–.78); Grafana and .98's own Alloy reach it locally. |
| Loki gRPC | 9095 | .98, **127.0.0.1 only** | Upstream default; single binary so nothing remote needs it. The ring is `inmemory`, so the memberlist port 7946 is **not** opened. |
| Alloy UI/metrics | 12345 | every Alloy host, **127.0.0.1 only** (free on rasp5 too) | Alloy default (already localhost-only). Debug UI: `ssh -L 12345:127.0.0.1:12345 192.168.1.<N>` then http://localhost:12345. |

---

## 3. Decisions and gotchas

### 3.1 `.99` sudo PATH gotcha
On the Rocky hosts, `sudo` resets `PATH` and drops `/usr/local/bin`. `sudo kubectl`,
`sudo crictl`, and `sudo helm` therefore fail with "command not found" on .99. **Always
use full paths:**
```bash
sudo /usr/local/bin/k3s kubectl ...
sudo /usr/local/bin/crictl ...
sudo /usr/local/bin/helm ...
```
(node_exporter installs to `/usr/local/bin/node_exporter`, but systemd runs it by
absolute path, so it's unaffected.)

### 3.2 Kubelet scrape auth on `.99` (chosen: ServiceAccount bearer token + *verified* TLS)
- The k3s kubelet rejects anonymous requests (`curl -k https://192.168.1.99:10250/metrics` → **401**).
  `insecure_skip_verify` **by itself is not an option**: it only skips checking the TLS certificate
  and does not authenticate.
- **Auth:** ServiceAccount `monitoring/prometheus-scraper`, bound to ClusterRole
  `prometheus-kubelet-scraper`, which grants **only** `get nodes/metrics` plus the
  `/metrics*` non-resource URLs. It can't list pods, read secrets, or do anything else.
  The long-lived token comes from a `kubernetes.io/service-account-token` Secret.
- **TLS:** verified, not skipped. The kubelet serving cert is issued by `k3s-server-ca` and has
  SAN `IP:192.168.1.99`, so Prometheus verifies it against a copy of
  `/var/lib/rancher/k3s/agent/server-ca.crt`.
- **Why not the k3s admin client cert:** that would give Prometheus cluster-admin.
- **Footprint on the cluster:** 5 new objects (Namespace, SA, ClusterRole, ClusterRoleBinding,
  Secret). No existing Deployment, Helm release, the vampi sidecar, or k3s itself was touched.
- **Scraped paths:** `/metrics` (kubelet health, running pods/containers) and `/metrics/cadvisor`
  (per-pod/container CPU, memory, network). `/metrics/resource` is **not** scraped because it
  duplicates cadvisor data.
- **metrics-server: not scraped.** It serves the `metrics.k8s.io` API (for `kubectl top`/HPA) by
  reading kubelet `/metrics/resource`, which we already cover at the source. Its own `/metrics` is
  only about itself, so scraping it adds nothing here.
- **Gap:** kube-state-metrics isn't deployed, so Deployment replica counts, restart counts, and
  pod phase aren't available. Pod resource usage is. Deploying kube-state-metrics would be the
  next step if you want those.

### 3.3 "Podman metrics endpoint" = prometheus-podman-exporter
Podman has no built-in Prometheus endpoint. The EPEL package `prometheus-podman-exporter`
provides one. The EPEL build uses libpod **in-process as root**, so `podman.socket` did
**not** need enabling (it's still inactive on all three hosts). `--collector.enhance-metrics`
adds `name`/`image`/`pod_name` labels to every metric so dashboards don't need joins.

### 3.4 Firewall approach
- **firewalld active** (.98, .101, .102): rich rules, added to runtime **and** permanent config
  **without `firewall-cmd --reload`**. A firewalld reload can flush the netavark/Docker container
  forwarding rules and cut off crAPI or vnotes until Podman/Docker re-create them.
- **firewalld inactive** (.99, .100): firewalld was **not** enabled, because that risks breaking
  k3s/flannel/kube-proxy/cloudflared and kong/jenkins networking. Instead there's a standalone
  nftables table `inet monitoring_acl`, loaded by its own systemd unit `monitoring-acl.service`.
  It only touches ports 9100/8081/9882 and never flushes other tables. (It deliberately does
  **not** use `nftables.service`, whose stop action runs `nft flush ruleset` and would wipe k3s's rules.)
- **Docker-published ports bypass firewalld** (Docker adds its own chains). That's why every new
  container (Prometheus, Grafana, Alertmanager, both cAdvisors) runs with `network_mode: host`:
  the host firewall then really governs access.

### 3.5 Prometheus config reload + bind mounts
Config is mounted as a **directory** (`/opt/monitoring/prometheus/config`), not a single file. A
single-file bind mount pins the original inode, and editors or `install`/`cp` that replace the
file would leave the container reading the stale copy. (This happened during setup and forced one
recreate.) Reload with no downtime:
```bash
curl -X POST http://localhost:9090/-/reload     # on .98; enabled by --web.enable-lifecycle
```

### 3.6 Dind-nested containers on .100
cAdvisor on .100 sees `jenkins` and `jenkins-docker`. Containers that Jenkins builds start
*inside* dind are nested and only show up as part of `jenkins-docker`'s totals.

### 3.7 Logs: Loki + Grafana Alloy (added 2026-09-19)
- **Why:** Grafana's **Drilldown → Logs** page (the `grafana-lokiexplore-app` plugin that ships with
  Grafana 13) needs a Loki data source; there wasn't one, so it only showed "add a Loki data source".
- **Loki** runs as a 6th service in the existing `/opt/monitoring` compose project, with `network_mode: host`
  like the others. It's single-binary with filesystem storage in `/opt/monitoring/loki/data` (uid 10001),
  TSDB schema v13, and **30-day retention** (same as Prometheus). The compactor enforces retention.
  `volume_enabled`, `pattern_ingester`, and `discover_log_levels` are on because Logs Drilldown uses them.
- **Agent: Grafana Alloy, not Promtail.** Promtail is end-of-life. Alloy is installed from the standalone
  GitHub RPM (checksum-verified), so no Grafana dnf repo was added. Upgrade by installing a newer RPM the same way.
- **Alloy runs as root** (systemd drop-in `alloy.service.d/10-lab.conf`). `/var/log/audit` (0700) and
  `/var/log/pods` (0750) are root-only, and the Docker socket is root-equivalent anyway. Alloy only
  *reads* logs and pushes out to .98. Its only listener is 127.0.0.1:12345.
- **Config is a directory** (`CONFIG_FILE="/etc/alloy"` in `/etc/sysconfig/alloy`): every `*.alloy` file
  in it is loaded. `common.alloy` is identical on all 5 hosts. `docker.alloy` is only on .98 and .100, and
  `k3s-pods.alloy` is only on .99. The RPM's sample `config.alloy` was renamed `config.alloy.rpm-default`
  so it isn't loaded.
- **What is collected:**

  | `job` | Source | Hosts | Notes |
  |---|---|---|---|
  | `journal` | systemd journal | all 5 | System services, sshd/sudo, kernel, **and every Podman container** (Podman's log driver is `journald` on all hosts, so crAPI, kong, and vampi-mcp arrive this way, labelled `container=<name>`). rsyslog copies the same data to `/var/log/messages`, so that file isn't read separately. |
  | `docker` | Docker API (`json-file` driver) | .98, .100, rasp5 | vnotes plus the monitoring stack on .98; jenkins and jenkins-docker on .100; the Twingate connector on rasp5. |
  | `k3s-pods` | `/var/log/pods/*/*/*.log` (CRI format) | .99 | Labels `namespace`, `pod`, `container`. Read from disk, so it needs no k8s API access and makes no cluster change. |
  | `audit` | `/var/log/audit/audit.log` | Rocky hosts | rasp5 has no auditd; the source just finds no file there, which is harmless. |

- **Labels:** every stream has `host` (= hostname, the same values as the Prometheus `host` label), `job`,
  and `service_name` (container name > systemd unit > syslog identifier; Drilldown groups by it). Journal
  streams add `unit` and `level` (from the journal priority). Container streams **drop** `level`,
  because stdout/stderr priority says nothing about severity. Loki then sets `detected_level` from the
  line text (ERROR, WARN, ...).
- **Cardinality guard:** Podman creates a unit per container (`libpod-conmon-<id>.scope`) and per healthcheck
  (`<64hex>-<hex>.service`), and logind creates one per login (`session-N.scope`). Alloy rewrites these to
  `podman-container`, `podman-healthcheck`, and `session.scope`. Result: about 25–40 streams per host.
- **Backfill:** on first start Alloy read the last **12h** of journal (`max_age`). Docker read existing
  container logs. Pod logs and `audit.log` start from the end (`tail_from_end`). Loki rejects lines
  older than 30d.
- **Ubuntu hosts** (rasp5, rk1–rk4) use the **same** `.alloy` files. Only the package differs: the `.deb` for
  `amd64`/`arm64`, with settings in `/etc/default/alloy` instead of `/etc/sysconfig/alloy`. rasp5's `host`
  label is `rasp5` (its hostname). Its journal is persistent (`/var/log/journal`, ~1 GB), so the first
  start backfilled 12h.
- **Logs Drilldown default data source:** the app falls back to Grafana's *default* data source (Prometheus)
  unless told otherwise, and then shows "Log volume has not been configured" even though Loki has
  `volume_enabled: true`. §5.19 pins it to Loki.
- **Volatile journals:** the journal on all 5 Rocky hosts is in `/run/log/journal` (no `/var/log/journal`), so
  logs from before a reboot exist **only in Loki** from now on. This setup didn't change that.
- **Volume (measured 2026-09-19):** about 5 KB/s, roughly 400 MiB/day raw across all 5 hosts (before Loki's
  compression). `ai-sim.service` on .102 is most of it (~38 lines/s of access logs). Alloy uses about
  240 MB RSS and 0.5–8% CPU per host.
- Loki is scraped by Prometheus (`job="loki"`), so the existing `TargetDown` alert covers it.

### 3.8 Log alerts (added 2026-09-19)
**Two layers:**
1. **Loki ruler** (built into Loki, config §5.13, rules §5.20) evaluates LogQL every 1m. It sends pattern
   alerts **straight to Alertmanager**, and it writes two **recording rules** to Prometheus over remote-write
   (Prometheus now runs with `--web.enable-remote-write-receiver`):
   - `lab:log_lines:rate5m{host,job}`: all log lines/s
   - `lab:log_error_lines:rate5m{host,service_name}`: error-or-worse lines/s (`detected_level` error/critical/fatal)
2. **Prometheus** (`rules/logs.yml`, §5.21) alerts on those series. It can compare them to history cheaply,
   which Loki can't do without re-scanning a day of logs every minute.

**Why no flat "more than N errors" alert:** in the baseline (6h on 2026-09-19), `crapi-workshop` on .101
logged about **93,000** error lines (the vulnerable lab, expected), while most services logged 0–100. A
fixed threshold either fires on crAPI all day or misses everything else. `LogErrorSpike` compares each
service with **its own** 1-day average instead.

| Alert | Where | Fires when | Severity |
|---|---|---|---|
| `LogErrorSpike` | Prometheus | a service's error rate is **> 3× its own 1-day average** and ≥ 0.2/s (12/min), for 10m. A service with no error history counts from 0, so a *new* error source at ≥ 12/min fires too. | warning |
| `LogsMissing` | Prometheus | a host that shipped journal logs in the last day has sent nothing for 20m+ (Alloy/host down, or .98:3100 unreachable) | warning |
| `LogSystemdUnitFailing` | Loki | the same unit logs "Failed with result" **≥ 3 times in 15m** (crash loop); label `failed_unit` | warning |
| `LogOOMKill` | Loki | the kernel OOM killer fired | critical |
| `LogDiskOrFilesystemError` | Loki | kernel I/O error, ext4/XFS error or corruption, or filesystem remounted read-only | critical |
| `LogSegfault` | Loki | kernel "segfault at" | warning |
| `LogJournalCritical` | Loki | any journal message at priority crit/alert/emerg | critical |
| `LogSSHBruteForce` | Loki | > 20 failed SSH logins on one host in 5m | warning |

All carry `source: logs`. Most annotations include a ready-to-paste Explore query.

**Where to see them:** Alertmanager http://192.168.1.98:9093, or Grafana → **Alerting → Alert rules**.
The Loki rules show under the *Loki* data source (read-only there: edit the files, not the UI).
**Notifications:** Alertmanager's receiver is still `"null"` (§5.4), so nothing is emailed or paged yet.
Add a receiver there to get notified.

**Baseline warm-up:** `LogErrorSpike` compares with up to 1 day of recorded history, and the series only
started 2026-09-19 ~15:15 UTC. For the first day the "normal" rate is based on less data.

**Known issue found while baselining (not changed):** `mcp-sweep.service` on .98 crash-looped roughly
140×/hour from ≤04:00 to 09:56 on 2026-09-19. `crapi_sweep.py` hits a 20s `ReadTimeout` against crapi-mcp
`192.168.1.102:8009`, exits 1, and `Restart=on-failure` restarts it. `LogSystemdUnitFailing` fires if it recurs.

**Test the pipeline** (fires `LogJournalCritical`, clears ~5 min later):
```bash
logger -p user.crit -t alert-test "TEST: log alert pipeline check"      # on any shipping host
curl -s http://192.168.1.98:9093/api/v2/alerts | grep -o '"alertname":"[^"]*"'   # ~1-2 min later
```
Verified 2026-09-19 from rasp5: fired in Alertmanager in about 90s.

---

## 4. Commands run, in order, per host

All commands run as `mcropsey` over SSH, with `sudo` where shown. Config file contents are in §5.

### 4.0 Step 1: port verification (read-only, .98–.102)
```bash
for h in 99 100 101 102 98; do
  ssh 192.168.1.$h 'hostname; sudo ss -tlnp'
done
# follow-up read-only checks
ssh 192.168.1.101 'sudo podman ps -a --format "{{.Names}}|{{.Status}}|{{.Ports}}"'
for h in 98 99 100 101 102; do
  ssh 192.168.1.$h 'systemctl is-active firewalld; sudo firewall-cmd --get-active-zones;
                    podman --version; docker --version; curl -sS -o /dev/null -w "%{http_code}\n" https://github.com'
done
curl -sk -o /dev/null -w "%{http_code}\n" https://192.168.1.99:10250/metrics     # 401 => auth required
ssh 192.168.1.99 'echo | openssl s_client -connect 127.0.0.1:10250 2>/dev/null | openssl x509 -noout -issuer -ext subjectAltName'
```

### 4.1 `.98`: monitoring stack (Prometheus, Grafana, Alertmanager, cAdvisor)
```bash
sudo mkdir -p /opt/monitoring/{prometheus/{config,data,rules,secrets},alertmanager/{config,data},grafana/{data,provisioning/{datasources,dashboards,plugins,alerting},dashboards,src},k3s}
# create the files from §5: docker-compose.yml, prometheus.yml, rules/basic.yml,
# alertmanager.yml, grafana provisioning (datasource + dashboards provider)
echo "GF_SECURITY_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -d /+=)" | sudo tee /opt/monitoring/.env >/dev/null
sudo chmod 600 /opt/monitoring/.env
sudo chown -R 65534:65534 /opt/monitoring/prometheus /opt/monitoring/alertmanager   # nobody (prom/alertmanager images)
sudo chown -R 472:0 /opt/monitoring/grafana                                         # grafana image uid
cd /opt/monitoring
sudo docker compose pull
sudo docker compose up -d
sudo docker compose ps
curl -s localhost:3000/api/health
curl -s localhost:8081/metrics | grep 'name="vnotes"' | head -1     # cAdvisor sees vnotes
```

### 4.2 All 5 hosts: node_exporter
Put the unit from §5.7 at `~/node_exporter.service`, then on **each** host:
```bash
V=1.12.1
cd "$(mktemp -d)"
curl -sSLO https://github.com/prometheus/node_exporter/releases/download/v${V}/node_exporter-${V}.linux-amd64.tar.gz
curl -sSLO https://github.com/prometheus/node_exporter/releases/download/v${V}/sha256sums.txt
grep "node_exporter-${V}.linux-amd64.tar.gz" sha256sums.txt | sha256sum -c -
tar xzf node_exporter-${V}.linux-amd64.tar.gz
sudo install -o root -g root -m 0755 node_exporter-${V}.linux-amd64/node_exporter /usr/local/bin/node_exporter
sudo restorecon -v /usr/local/bin/node_exporter
id node_exporter &>/dev/null || sudo useradd --system --no-create-home --shell /sbin/nologin node_exporter
sudo install -o root -g root -m 0644 ~/node_exporter.service /etc/systemd/system/node_exporter.service
sudo systemctl daemon-reload
sudo systemctl enable --now node_exporter
curl -s localhost:9100/metrics | grep '^node_exporter_build_info'
```

### 4.3 `.100`: cAdvisor
```bash
sudo mkdir -p /opt/cadvisor
# create /opt/cadvisor/docker-compose.yml from §5.9
cd /opt/cadvisor && sudo docker compose up -d
curl -s localhost:8081/metrics | grep '^container_memory_usage_bytes' | grep -oE 'name="[^"]+"' | sort -u
#   -> cadvisor, jenkins, jenkins-docker
```

### 4.4 `.100`, `.101`, `.102`: prometheus-podman-exporter
```bash
sudo dnf install -y prometheus-podman-exporter          # EPEL 9
echo 'PODMAN_EXPORTER_OPTS="--collector.enable-all --collector.enhance-metrics --web.listen-address=:9882"' \
  | sudo tee /etc/sysconfig/prometheus-podman-exporter
sudo systemctl enable --now prometheus-podman-exporter
curl -s localhost:9882/metrics | grep '^podman_container_info' | grep -oE ' name="[^"]+"'
#   .100 -> kong   .101 -> 12 crAPI-stack containers   .102 -> vampi-mcp
```
(podman.socket was **not** enabled; it's not needed.)

### 4.5 Firewall
**.98** (firewalld): Grafana/Prometheus/Alertmanager for the LAN, exporters for .98 itself:
```bash
add() { # $1=source $2=port   (runtime + permanent, NO --reload)
  R="rule family=\"ipv4\" source address=\"$1\" port port=\"$2\" protocol=\"tcp\" accept"
  sudo firewall-cmd --zone=public --add-rich-rule="$R"
  sudo firewall-cmd --permanent --zone=public --add-rich-rule="$R"
}
for p in 3000 9090 9093; do add 192.168.1.0/24 $p; done
for p in 9100 8081;      do add 192.168.1.98   $p; done
```
**.101 and .102** (firewalld), using the same `add` function:
```bash
for p in 9100 9882; do add 192.168.1.98 $p; done
```
**.99 and .100** (no firewalld): standalone nft ACL. Put the files from §5.10/§5.11 at `~/`, then:
```bash
sudo install -m 0644 ~/monitoring-acl.nft /etc/nftables/monitoring-acl.nft
sudo install -m 0644 ~/monitoring-acl.service /etc/systemd/system/monitoring-acl.service
sudo nft -c -f /etc/nftables/monitoring-acl.nft          # syntax check
sudo systemctl daemon-reload
sudo systemctl enable --now monitoring-acl
sudo nft list table inet monitoring_acl
```

### 4.6 `.99`: kubelet scrape identity (then copy token + CA to .98)
```bash
# on .99: manifest from §5.12 at ~/prometheus-scraper-rbac.yaml
K="sudo /usr/local/bin/k3s kubectl"
$K apply -f ~/prometheus-scraper-rbac.yaml
$K -n monitoring get secret prometheus-scraper-token -o jsonpath='{.data.token}' | base64 -d > ~/k3s-scraper.token
sudo cp /var/lib/rancher/k3s/agent/server-ca.crt ~/k3s-server-ca.crt && sudo chown mcropsey ~/k3s-server-ca.crt
chmod 600 ~/k3s-scraper.token
$K auth can-i list pods --as=system:serviceaccount:monitoring:prometheus-scraper    # -> no (least privilege)

# from the admin box: move them to .98 and delete from .99
scp 192.168.1.99:k3s-scraper.token 192.168.1.99:k3s-server-ca.crt . && ssh 192.168.1.99 'rm -f ~/k3s-scraper.token ~/k3s-server-ca.crt'
scp k3s-scraper.token k3s-server-ca.crt 192.168.1.98: && rm -f k3s-scraper.token

# on .98
sudo install -o 65534 -g 65534 -m 0400 ~/k3s-scraper.token /opt/monitoring/prometheus/secrets/k3s-scraper.token
sudo install -o 65534 -g 65534 -m 0444 ~/k3s-server-ca.crt /opt/monitoring/prometheus/secrets/k3s-server-ca.crt
rm -f ~/k3s-scraper.token ~/k3s-server-ca.crt
for p in /metrics /metrics/cadvisor; do
  sudo curl -s -o /dev/null -w "$p %{http_code}\n" --cacert /opt/monitoring/prometheus/secrets/k3s-server-ca.crt \
    -H "Authorization: Bearer $(sudo cat /opt/monitoring/prometheus/secrets/k3s-scraper.token)" https://192.168.1.99:10250$p
done   # -> 200 200 with verified TLS
```

### 4.7 `.98`: final Prometheus config + zero-downtime reload
```bash
# write /opt/monitoring/prometheus/config/prometheus.yml from §5.2
sudo docker run --rm \
  -v /opt/monitoring/prometheus/config:/etc/prometheus/config:ro,Z \
  -v /opt/monitoring/prometheus/rules:/etc/prometheus/rules:ro,Z \
  -v /opt/monitoring/prometheus/secrets:/etc/prometheus/secrets:ro,Z \
  --entrypoint promtool prom/prometheus:v3.14.0 check config /etc/prometheus/config/prometheus.yml
curl -X POST localhost:9090/-/reload
curl -s localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"' | sort | uniq -c   # -> 14 x "up"
```

### 4.8 `.98`: Grafana dashboards
Dashboards are generated as provisioning JSON by `monitoring/gen_dashboards.py` (copy also at
`/opt/monitoring/grafana/src/` on .98). Node Exporter Full is grafana.com dashboard **1860, revision 45**.
```bash
curl -s -o 1860.json https://grafana.com/api/dashboards/1860/revisions/latest/download
python3 gen_dashboards.py out 1860.json               # writes out/<Folder Name>/<dash>.json
scp -r out 192.168.1.98:dash-staging
ssh 192.168.1.98 'sudo rm -rf /opt/monitoring/grafana/dashboards/* &&
  sudo cp -r ~/dash-staging/. /opt/monitoring/grafana/dashboards/ &&
  sudo chown -R 472:0 /opt/monitoring/grafana/dashboards && rm -rf ~/dash-staging'
# Grafana picks up changes within 30s (updateIntervalSeconds); no restart.
```

### 4.9 Validation
```bash
# every target UP
ssh 192.168.1.98 'curl -s localhost:9090/api/v1/targets' | python3 -c '
import json,sys
for t in json.load(sys.stdin)["data"]["activeTargets"]:
    print(t["health"], t["labels"]["job"], t["scrapeUrl"], t["lastError"])'
# exporter ACL: blocked from any other LAN host, 200 from .98
curl -m4 http://192.168.1.101:9100/metrics                              # from NOT-.98 -> timeout
ssh 192.168.1.98 'curl -s -o /dev/null -w "%{http_code}\n" http://192.168.1.101:9100/metrics'  # -> 200
# every dashboard panel query returns data through Grafana
ssh 192.168.1.98 'sudo grep -oP "(?<=GF_SECURITY_ADMIN_PASSWORD=).*" /opt/monitoring/.env' > .gfpw
python3 monitoring/validate_grafana.py http://192.168.1.98:3000 .gfpw; rm .gfpw
```

### 4.10 Logs: Loki on `.98`, Alloy on all 5 (2026-09-19)
Pre-check (read-only): ports 3100/9095/12345 free everywhere, log drivers, disk:
```bash
for h in 98 99 100 101 102; do ssh 192.168.1.$h 'hostname; sudo ss -tlnp | grep -E ":(3100|9095|12345|7946) "; df -h /;
  sudo podman info --format "{{.Host.LogDriver}}"; sudo docker info --format "{{.LoggingDriver}}" 2>/dev/null'; done
```
**.98 Loki** (config from §5.13; compose service from §5.1):
```bash
sudo cp -a /opt/monitoring/docker-compose.yml /opt/monitoring/docker-compose.yml.bak-20260919
sudo mkdir -p /opt/monitoring/loki/{config,data}
sudo install -m 0644 ~/loki.yml /opt/monitoring/loki/config/loki.yml
sudo chown -R 10001:10001 /opt/monitoring/loki                    # grafana/loki image uid
# add the `loki:` service (§5.1) to docker-compose.yml, then
cd /opt/monitoring && sudo docker compose config -q && sudo docker compose up -d loki
curl -s localhost:3100/ready                                      # -> ready (after ~30s)
# firewall: only the Alloy agents on .99-.102 may push (no --reload; `add` from §4.5)
for s in 99 100 101 102; do add 192.168.1.$s 3100; done
# Grafana data source (§5.14), picked up without restarting Grafana
sudo install -o 472 -g 0 -m 0644 ~/loki-ds.yml /opt/monitoring/grafana/provisioning/datasources/loki.yml
curl -s -u admin:$PW -X POST localhost:3000/api/admin/provisioning/datasources/reload
curl -s -u admin:$PW localhost:3000/api/datasources/uid/loki/health      # -> "Data source successfully connected."
# Prometheus: add the `loki` job (§5.2), promtool check (§4.7), then
curl -X POST localhost:9090/-/reload
```
**Every host: Alloy.** Copy `monitoring/alloy/common.alloy` and `alloy-root.conf`, plus `docker.alloy`
(.98, .100) or `k3s-pods.alloy` (.99), to `~/`, then:
```bash
V=1.19.2
cd "$(mktemp -d)"
curl -sSLO https://github.com/grafana/alloy/releases/download/v${V}/alloy-${V}-1.amd64.rpm
curl -sSLO https://github.com/grafana/alloy/releases/download/v${V}/SHA256SUMS
grep " alloy-${V}-1.amd64.rpm$" SHA256SUMS | sha256sum -c -
sudo dnf install -y ./alloy-${V}-1.amd64.rpm
sudo mkdir -p /etc/systemd/system/alloy.service.d
sudo install -m 0644 ~/alloy-root.conf /etc/systemd/system/alloy.service.d/10-lab.conf
sudo mv /etc/alloy/config.alloy /etc/alloy/config.alloy.rpm-default
sudo install -m 0644 ~/common.alloy /etc/alloy/common.alloy        # + docker.alloy / k3s-pods.alloy where applicable
sudo sed -i 's|^CONFIG_FILE=.*|CONFIG_FILE="/etc/alloy"|' /etc/sysconfig/alloy
sudo alloy fmt /etc/alloy/common.alloy >/dev/null && echo syntax ok
sudo systemctl daemon-reload && sudo systemctl enable --now alloy
sudo ss -tlnp | grep alloy                                         # -> 127.0.0.1:12345 only
curl -s localhost:12345/api/v0/web/components | python3 -c 'import json,sys; [print(c["localID"], c["health"]["state"]) for c in json.load(sys.stdin)]'
```
Change an Alloy config later by editing `/etc/alloy/*.alloy`, then `sudo systemctl reload alloy`.
**Re-reading the journal:** stop alloy, delete `/var/lib/alloy/data/loki.source.journal.journal/positions.yml`,
and start alloy. It re-reads the last 12h, and that data is **duplicated** in Loki (this was done once on
2026-09-19 to fix a label; the old streams were removed with the delete API, see §4.11).

**Dashboards:** regenerate and redeploy per §4.8. The generator now also writes `Logs/logs.json` and a
log panel on every host dashboard.

### 4.12 Ubuntu hosts: rasp5 (done), rk1–rk4 (pending, hosts offline)
All from rasp5, in `~/monitoring/alloy/`. `install-alloy-ubuntu.sh` detects `amd64`/`arm64`, verifies the
checksum, installs the `.deb`, installs `common.alloy` plus `docker.alloy` (only if Docker is active) and
`k3s-pods.alloy` (only if `/var/log/pods` exists), points `/etc/default/alloy` at the directory, and
starts Alloy. It's idempotent: re-running it re-installs the same version.
```bash
# .98: allow the host to push (done for .85 and .75-.78 on 2026-09-19; `add` from §4.5)
for s in 85 75 76 77 78; do add 192.168.1.$s 3100; done
# rasp5 itself (done 2026-09-19)
./install-alloy-ubuntu.sh --local
# rk1-rk4: TODO once they're back online (they were unreachable 2026-09-19)
./install-alloy-ubuntu.sh rk1 rk2 rk3 rk4
# check: the new hosts appear
ssh 192.168.1.98 'curl -s localhost:3100/loki/api/v1/label/host/values'
```
The rk hosts are SSH spokes (they hold no private key, by design); Alloy only needs outbound HTTP to .98:3100,
so that stays the same. If ufw gets turned on there, it needs no inbound rule either.

### 4.13 Log alerts (2026-09-19)
```bash
# .98: Loki ruler + rules dir, Prometheus remote-write receiver
cd /opt/monitoring
sudo cp -a docker-compose.yml docker-compose.yml.bak-20260919b
sudo cp -a loki/config/loki.yml loki/loki.yml.bak-20260919
sudo mkdir -p loki/rules/fake
sudo install -o 10001 -g 10001 -m 0644 ~/loki.yml loki/config/loki.yml            # §5.13 (adds ruler:)
sudo install -o 10001 -g 10001 -m 0644 ~/lab-logs.yml loki/rules/fake/lab-logs.yml  # §5.20
sudo install -o 65534 -g 65534 -m 0644 ~/logs.yml prometheus/rules/logs.yml         # §5.21
sudo chown 10001:10001 loki/rules loki/rules/fake
# docker-compose.yml (§5.1): prometheus gets --web.enable-remote-write-receiver,
# loki gets the /opt/monitoring/loki/rules:/etc/loki/rules mount
sudo docker run --rm -v ~/loki.yml:/c/loki.yml:ro,Z grafana/loki:3.7.8 -config.file=/c/loki.yml -verify-config
python3 -c 'import yaml; yaml.safe_load(open("lab-logs.yml"))'   # quote-check: use `expr: |` blocks
sudo docker compose up -d prometheus loki     # recreates both (a few seconds of scrape/ingest gap; Alloy buffers)
# check
curl -s localhost:3100/prometheus/api/v1/rules      # 8 rules, health "ok"
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=count by (__name__) ({__name__=~"lab:log_.*"})'
```
**Changing log rules later:** edit `loki/rules/fake/lab-logs.yml` on .98. The ruler re-reads it within about 1
min, with no restart. Prometheus-side rules (`prometheus/rules/logs.yml`): promtool check (§4.7), then
`curl -X POST localhost:9090/-/reload`. **Silence** noisy alerts in the Alertmanager UI rather than deleting rules.

### 4.11 Validation (logs)
```bash
# every host shipping, per job
ssh 192.168.1.98 'curl -sG localhost:3100/loki/api/v1/query --data-urlencode "query=sum by (host,job) (count_over_time({job=~\".+\"}[10m]))"'
# Drilldown's backend (volume + patterns) works
ssh 192.168.1.98 'curl -sG localhost:3100/loki/api/v1/index/volume --data-urlencode "query={service_name=~\".+\"}"'
# Loki is NOT reachable from other LAN hosts (rasp5 is now an allowed shipper, so test from a different box)
curl -m4 http://192.168.1.98:3100/ready                         # from e.g. your workstation -> timeout
# dashboards (validate_grafana.py now runs Loki queries too)
python3 monitoring/validate_grafana.py http://192.168.1.98:3000 .gfpw
# delete streams by selector (e.g. after a labelling mistake); applied by the compactor after ~24h
curl -XPOST -G localhost:3100/loki/api/v1/delete --data-urlencode 'query={job="bad"}' --data-urlencode "start=$(date -d -2days +%s)"
```

---

## 5. Final config files (verbatim from the hosts)

### 5.1 `.98` `/opt/monitoring/docker-compose.yml`
```yaml
# /opt/monitoring/docker-compose.yml  (host: 192.168.1.98, hv-rocky-linux-1)
# All services use host networking so firewalld (not Docker's own iptables
# chains) governs who can reach 9090/9093/3000/8081/3100.
name: monitoring

services:
  prometheus:
    image: prom/prometheus:v3.14.0
    container_name: prometheus
    network_mode: host
    restart: unless-stopped
    user: "65534:65534"
    command:
      - --config.file=/etc/prometheus/config/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=30d
      - --web.listen-address=0.0.0.0:9090
      - --web.enable-lifecycle          # allows POST /-/reload (zero-downtime reload)
      - --web.enable-remote-write-receiver   # Loki ruler writes lab:log_* series here
    volumes:
      # directory (not single-file) mount: editors/`install` replace the inode,
      # and a file mount would keep serving the stale copy on /-/reload
      - /opt/monitoring/prometheus/config:/etc/prometheus/config:ro,Z
      - /opt/monitoring/prometheus/rules:/etc/prometheus/rules:ro,Z
      - /opt/monitoring/prometheus/secrets:/etc/prometheus/secrets:ro,Z
      - /opt/monitoring/prometheus/data:/prometheus:Z

  alertmanager:
    image: prom/alertmanager:v0.34.1
    container_name: alertmanager
    network_mode: host
    restart: unless-stopped
    user: "65534:65534"
    command:
      - --config.file=/etc/alertmanager/config/alertmanager.yml
      - --storage.path=/alertmanager
      - --web.listen-address=0.0.0.0:9093
      - --cluster.listen-address=        # single instance, disable gossip port 9094
    volumes:
      - /opt/monitoring/alertmanager/config:/etc/alertmanager/config:ro,Z
      - /opt/monitoring/alertmanager/data:/alertmanager:Z

  grafana:
    image: grafana/grafana:13.2.2
    container_name: grafana
    network_mode: host
    restart: unless-stopped
    user: "472:0"
    env_file: /opt/monitoring/.env        # GF_SECURITY_ADMIN_PASSWORD
    environment:
      GF_SERVER_HTTP_PORT: "3000"
      GF_SECURITY_ADMIN_USER: admin
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_ANALYTICS_REPORTING_ENABLED: "false"
      GF_ANALYTICS_CHECK_FOR_UPDATES: "false"
    volumes:
      - /opt/monitoring/grafana/data:/var/lib/grafana:Z
      - /opt/monitoring/grafana/provisioning:/etc/grafana/provisioning:ro,Z
      - /opt/monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro,Z

  loki:                                   # added 2026-09-19
    image: grafana/loki:3.7.8
    container_name: loki
    network_mode: host
    restart: unless-stopped
    user: "10001:10001"
    command:
      - -config.file=/etc/loki/config/loki.yml
    volumes:
      # directory mount, same reason as prometheus/config
      - /opt/monitoring/loki/config:/etc/loki/config:ro,Z
      - /opt/monitoring/loki/rules:/etc/loki/rules:ro,Z
      - /opt/monitoring/loki/data:/loki:Z

  cadvisor:
    image: ghcr.io/google/cadvisor:v0.60.6
    container_name: cadvisor
    network_mode: host
    restart: unless-stopped
    privileged: true
    devices:
      - /dev/kmsg
    command:
      - --port=8081
      - --docker_only=true
      - --housekeeping_interval=15s
      - --store_container_labels=false
      - --whitelisted_container_labels=com.docker.compose.project,com.docker.compose.service
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
      - /dev/disk/:/dev/disk:ro
```

### 5.2 `.98` `/opt/monitoring/prometheus/config/prometheus.yml`
```yaml
# /opt/monitoring/prometheus/prometheus.yml  (host: 192.168.1.98)
global:
  scrape_interval: 30s
  scrape_timeout: 10s
  evaluation_interval: 30s
  external_labels:
    lab: rocky-hv

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["localhost:9093"]

rule_files:
  - /etc/prometheus/rules/*.yml

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
        labels: {host: hv-rocky-linux-1}

  - job_name: alertmanager
    static_configs:
      - targets: ["localhost:9093"]
        labels: {host: hv-rocky-linux-1}

  - job_name: loki                        # added 2026-09-19
    static_configs:
      - targets: ["localhost:3100"]
        labels: {host: hv-rocky-linux-1}

  # ---- node_exporter on all 5 hosts --------------------------------------
  - job_name: node
    static_configs:
      - targets: ["192.168.1.98:9100"]
        labels: {host: hv-rocky-linux-1, role: "monitoring + traffic-gen"}
      - targets: ["192.168.1.99:9100"]
        labels: {host: hv-rocky-linux-2, role: "k3s"}
      - targets: ["192.168.1.100:9100"]
        labels: {host: hv-rocky-linux-3, role: "gateway + CI"}
      - targets: ["192.168.1.101:9100"]
        labels: {host: hv-rocky-linux-4, role: "crAPI vuln lab"}
      - targets: ["192.168.1.102:9100"]
        labels: {host: hv-rocky-linux-5, role: "MCP + AI sim"}

  # ---- cAdvisor (Docker containers) ---------------------------------------
  - job_name: cadvisor
    static_configs:
      - targets: ["192.168.1.98:8081"]
        labels: {host: hv-rocky-linux-1}
      - targets: ["192.168.1.100:8081"]
        labels: {host: hv-rocky-linux-3}
    metric_relabel_configs:
      # keep only real containers (named), drop cgroup slices
      - source_labels: [__name__, name]
        regex: "container_.+;"
        action: drop

  # ---- prometheus-podman-exporter ----------------------------------------
  - job_name: podman
    static_configs:
      - targets: ["192.168.1.100:9882"]
        labels: {host: hv-rocky-linux-3}
      - targets: ["192.168.1.101:9882"]
        labels: {host: hv-rocky-linux-4, environment: "vulnerable-app security lab"}
      - targets: ["192.168.1.102:9882"]
        labels: {host: hv-rocky-linux-5}

  # ---- k3s kubelet on .99 (direct, no kube-prometheus-stack) --------------
  # Auth: bearer token of SA monitoring/prometheus-scraper (ClusterRole:
  #   get nodes/metrics only). TLS verified against k3s server CA; kubelet
  #   serving cert has IP SAN 192.168.1.99.
  - job_name: k3s-kubelet
    scheme: https
    metrics_path: /metrics
    authorization:
      credentials_file: /etc/prometheus/secrets/k3s-scraper.token
    tls_config:
      ca_file: /etc/prometheus/secrets/k3s-server-ca.crt
    static_configs:
      - targets: ["192.168.1.99:10250"]
        labels: {host: hv-rocky-linux-2, node: hv-rocky-linux-2}

  - job_name: k3s-cadvisor
    scheme: https
    metrics_path: /metrics/cadvisor
    authorization:
      credentials_file: /etc/prometheus/secrets/k3s-scraper.token
    tls_config:
      ca_file: /etc/prometheus/secrets/k3s-server-ca.crt
    static_configs:
      - targets: ["192.168.1.99:10250"]
        labels: {host: hv-rocky-linux-2, node: hv-rocky-linux-2}
    metric_relabel_configs:
      # high-cardinality, unused by dashboards
      - source_labels: [__name__]
        regex: "container_(tasks_state|memory_failures_total|blkio_device_usage_total)"
        action: drop
```

### 5.3 `.98` `/opt/monitoring/prometheus/rules/basic.yml`
```yaml
# /opt/monitoring/prometheus/rules/basic.yml
groups:
  - name: lab-basic
    rules:
      - alert: TargetDown
        expr: up == 0
        for: 2m
        labels: {severity: warning}
        annotations:
          summary: "{{ $labels.job }} target {{ $labels.instance }} is down"
      - alert: HostDiskAlmostFull
        expr: (node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"} / node_filesystem_size_bytes) < 0.10
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "{{ $labels.instance }} {{ $labels.mountpoint }} has <10% free"
      - alert: HostHighMemory
        expr: (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) > 0.95
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "{{ $labels.instance }} memory usage >95%"
```

### 5.4 `.98` `/opt/monitoring/alertmanager/config/alertmanager.yml`
```yaml
# /opt/monitoring/alertmanager/alertmanager.yml
# No notification channel chosen yet: alerts are visible in the Alertmanager UI
# (http://192.168.1.98:9093) and Grafana. Add an email/webhook receiver later.
route:
  receiver: "null"
  group_by: [alertname, instance]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
receivers:
  - name: "null"
```

### 5.5 `.98` `/opt/monitoring/grafana/provisioning/datasources/prometheus.yml`
Prometheus is the **default** data source (`isDefault: true`, uid `prometheus`).
```yaml
# /opt/monitoring/grafana/provisioning/datasources/prometheus.yml
apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: false
    jsonData:
      timeInterval: 30s
      alertmanagerUid: alertmanager
  - name: Alertmanager
    uid: alertmanager
    type: alertmanager
    access: proxy
    url: http://localhost:9093
    jsonData:
      implementation: prometheus
```

### 5.6 `.98` `/opt/monitoring/grafana/provisioning/dashboards/folders.yml`
```yaml
# /opt/monitoring/grafana/provisioning/dashboards/folders.yml
# Each subdirectory of /var/lib/grafana/dashboards becomes a Grafana folder.
apiVersion: 1
providers:
  - name: lab-dashboards
    type: file
    disableDeletion: true
    allowUiUpdates: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: true
```
`.98` `/opt/monitoring/.env` (mode 600): `GF_SECURITY_ADMIN_PASSWORD=<random>`

`.98` `/opt/monitoring/prometheus/secrets/`: `k3s-scraper.token` (0400, uid 65534) and
`k3s-server-ca.crt` (0444), both from §4.6.

### 5.7 All 5 hosts `/etc/systemd/system/node_exporter.service` (identical everywhere)
```ini
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
ExecStart=/usr/local/bin/node_exporter \
  --web.listen-address=:9100 \
  --collector.systemd \
  --collector.systemd.unit-include=(k3s|cloudflared|docker|podman|prometheus-podman-exporter|node_exporter|mcp-heartbeat|mcp-sweep|ai-sim|crapi-mcp|noname-mcp|noname-sensor)\.service \
  --collector.filesystem.mount-points-exclude=^/(dev|proc|run|sys|var/lib/docker/.+|var/lib/containers/.+|var/lib/kubelet/.+|run/k3s/.+)($|/)
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
```

### 5.8 `.100/.101/.102` prometheus-podman-exporter
`/etc/sysconfig/prometheus-podman-exporter`:
```bash
PODMAN_EXPORTER_OPTS="--collector.enable-all --collector.enhance-metrics --web.listen-address=:9882"
```
Unit (shipped by the EPEL RPM, unmodified) `/usr/lib/systemd/system/prometheus-podman-exporter.service`:
```ini
[Unit]
Description=Prometheus exporter for podman (v5) machine
[Service]
Restart=on-failure
EnvironmentFile=-/etc/sysconfig/prometheus-podman-exporter
ExecStart=/usr/bin/prometheus-podman-exporter $PODMAN_EXPORTER_OPTS
ExecReload=/bin/kill -HUP $MAINPID
TimeoutStopSec=20s
SendSIGKILL=no
[Install]
WantedBy=default.target
```

### 5.9 `.100` `/opt/cadvisor/docker-compose.yml`
```yaml
# /opt/cadvisor/docker-compose.yml  (host: 192.168.1.100, hv-rocky-linux-3)
# Docker-only view: jenkins + jenkins-docker (dind). Kong (Podman) is covered by
# prometheus-podman-exporter on :9882. Host networking so the nft ACL applies.
name: cadvisor
services:
  cadvisor:
    image: ghcr.io/google/cadvisor:v0.60.6
    container_name: cadvisor
    network_mode: host
    restart: unless-stopped
    privileged: true
    devices:
      - /dev/kmsg
    command:
      - --port=8081
      - --docker_only=true
      - --housekeeping_interval=15s
      - --store_container_labels=false
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
      - /dev/disk/:/dev/disk:ro
```

### 5.10 `.99` and `.100` `/etc/nftables/monitoring-acl.nft` (identical, sha256 `7f8d905eeca2af85…`)
```
#!/usr/sbin/nft -f
# /etc/nftables/monitoring-acl.nft
# Standalone ACL for exporter ports on hosts where firewalld is NOT running
# (.99 k3s, .100 kong/jenkins). Lives in its own table so it never touches the
# iptables-nft rules owned by k3s/kube-proxy/flannel/Docker/netavark.
# Idempotent: the first two lines make re-loading safe.
table inet monitoring_acl
delete table inet monitoring_acl

table inet monitoring_acl {
    set exporter_ports {
        type inet_service
        elements = { 9100, 8081, 9882 }    # node_exporter, cAdvisor, podman-exporter
    }
    chain input {
        type filter hook input priority filter - 5; policy accept;
        iifname "lo" tcp dport @exporter_ports accept
        ip saddr 192.168.1.98 tcp dport @exporter_ports accept   # Prometheus host
        tcp dport @exporter_ports counter drop                    # everyone else (v4 + v6)
    }
}
```

### 5.11 `.99` and `.100` `/etc/systemd/system/monitoring-acl.service`
```ini
# /etc/systemd/system/monitoring-acl.service
[Unit]
Description=nftables ACL: exporter ports reachable only from 192.168.1.98
After=network-pre.target
Before=network-online.target node_exporter.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables/monitoring-acl.nft
ExecStop=/usr/sbin/nft delete table inet monitoring_acl

[Install]
WantedBy=multi-user.target
```

### 5.12 `.99` k3s scrape auth: `prometheus-scraper-rbac.yaml`
(Applied with `sudo /usr/local/bin/k3s kubectl apply -f`. Copies at `/opt/monitoring/k3s/` on .98 and `monitoring/` here.)
```yaml
# prometheus-scraper-rbac.yaml — applied on .99 (k3s). Additive only.
# Lets the external Prometheus on 192.168.1.98 read kubelet /metrics,
# /metrics/cadvisor, /metrics/resource, /metrics/probes. Nothing else.
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus-scraper
  namespace: monitoring
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: prometheus-kubelet-scraper
rules:
  - apiGroups: [""]
    resources: ["nodes/metrics"]
    verbs: ["get"]
  - nonResourceURLs: ["/metrics", "/metrics/cadvisor", "/metrics/resource", "/metrics/probes"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: prometheus-kubelet-scraper
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: prometheus-kubelet-scraper
subjects:
  - kind: ServiceAccount
    name: prometheus-scraper
    namespace: monitoring
---
# Long-lived token (no expiry). Rotate by deleting this Secret and re-applying.
apiVersion: v1
kind: Secret
metadata:
  name: prometheus-scraper-token
  namespace: monitoring
  annotations:
    kubernetes.io/service-account.name: prometheus-scraper
type: kubernetes.io/service-account-token
```
Rotate the token: `sudo /usr/local/bin/k3s kubectl -n monitoring delete secret prometheus-scraper-token`,
re-apply the manifest, then redo the copy steps in §4.6 and `curl -X POST localhost:9090/-/reload` on .98.

### 5.13 `.98` `/opt/monitoring/loki/config/loki.yml`
```yaml
# /opt/monitoring/loki/config/loki.yml  (host: 192.168.1.98, hv-rocky-linux-1)
# Single-binary Loki, filesystem storage, 30-day retention (same as Prometheus).
# Only :3100 (HTTP) is reachable from the LAN; gRPC is bound to localhost and
# the ring is in-memory, so no memberlist port (7946) is opened.
auth_enabled: false

server:
  http_listen_address: 0.0.0.0
  http_listen_port: 3100
  grpc_listen_address: 127.0.0.1
  grpc_listen_port: 9095
  log_level: warn

common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules

schema_config:
  configs:
    - from: 2026-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

limits_config:
  retention_period: 30d
  reject_old_samples: true
  reject_old_samples_max_age: 30d
  ingestion_rate_mb: 16
  ingestion_burst_size_mb: 32
  allow_structured_metadata: true
  volume_enabled: true                 # needed by Grafana Logs Drilldown
  discover_log_levels: true
  discover_service_name: [service_name, container, unit, job]

pattern_ingester:
  enabled: true                        # "Patterns" tab in Logs Drilldown

compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  delete_request_store: filesystem

ruler:
  # log alerts -> Alertmanager; recording rules (lab:log_*) -> Prometheus remote-write
  alertmanager_url: http://localhost:9093
  enable_api: true                     # Grafana lists these rules under Alerting
  evaluation_interval: 1m
  rule_path: /loki/ruler-scratch
  storage:
    type: local
    local:
      directory: /etc/loki/rules      # <dir>/fake/*.yml ("fake" = tenant when auth is off)
  wal:
    dir: /loki/ruler-wal
  remote_write:
    enabled: true
    clients:
      prometheus:
        url: http://localhost:9090/api/v1/write

analytics:
  reporting_enabled: false
```

### 5.14 `.98` `/opt/monitoring/grafana/provisioning/datasources/loki.yml`
```yaml
# /opt/monitoring/grafana/provisioning/datasources/loki.yml
apiVersion: 1
datasources:
  - name: Loki
    uid: loki
    type: loki
    access: proxy
    url: http://localhost:3100
    editable: false
    jsonData:
      maxLines: 5000
```

### 5.15 All 5 hosts `/etc/alloy/common.alloy` (identical everywhere)
```alloy
// /etc/alloy/common.alloy  (identical on all 5 hosts)
// Ships the systemd journal (which also holds syslog, sshd/sudo, and every
// Podman container's stdout/stderr - Podman's log driver is journald here)
// plus auditd to Loki on .98. Every stream gets host=<hostname>, matching the
// `host` label Prometheus uses.

loki.write "lab" {
  endpoint {
    url = "http://192.168.1.98:3100/loki/api/v1/push"
  }
  external_labels = {host = constants.hostname}
}

// ---- journald ----------------------------------------------------------
loki.relabel "journal" {
  forward_to = []

  // loki.source.journal ignores `labels` for job once relabel_rules is set
  // (it would be "loki.source.journal.journal"), so set it here.
  rule {
    target_label = "job"
    replacement  = "journal"
  }
  rule {
    source_labels = ["__journal__systemd_unit"]
    target_label  = "unit"
  }
  // Collapse per-container / per-session units so they don't explode into
  // thousands of streams: libpod-conmon-<id>.scope, <64-hex>-<hex>.service (podman
  // healthchecks), session-<n>.scope.
  rule {
    source_labels = ["__journal__systemd_unit"]
    regex         = "libpod-conmon-.*\\.scope"
    target_label  = "unit"
    replacement   = "podman-container"
  }
  rule {
    source_labels = ["__journal__systemd_unit"]
    regex         = "[0-9a-f]{64}(-[0-9a-f]+)?\\.(service|timer|scope)"
    target_label  = "unit"
    replacement   = "podman-healthcheck"
  }
  rule {
    source_labels = ["__journal__systemd_unit"]
    regex         = "session-[0-9a-z]+\\.scope"
    target_label  = "unit"
    replacement   = "session.scope"
  }
  rule {
    source_labels = ["__journal_priority_keyword"]
    target_label  = "level"
  }
  rule {
    source_labels = ["__journal_container_name"]
    target_label  = "container"
  }
  // Container stdout/stderr arrives as priority info/err regardless of the
  // text; drop `level` so Loki detects it from the line (ERROR, WARN...).
  rule {
    source_labels = ["container"]
    regex         = ".+"
    target_label  = "level"
    replacement   = ""
  }
  // service_name (what Grafana Logs Drilldown groups by):
  // container name > unit > syslog identifier
  rule {
    source_labels = ["__journal_syslog_identifier"]
    target_label  = "service_name"
  }
  rule {
    source_labels = ["unit"]
    regex         = "(.+)"
    target_label  = "service_name"
  }
  rule {
    source_labels = ["container"]
    regex         = "(.+)"
    target_label  = "service_name"
  }
}

loki.source.journal "journal" {
  max_age       = "12h"               // backfill window on first start
  relabel_rules = loki.relabel.journal.rules
  forward_to    = [loki.write.lab.receiver]
}

// ---- auditd --------------------------------------------------------------
local.file_match "audit" {
  path_targets = [{"__path__" = "/var/log/audit/audit.log", "job" = "audit", "service_name" = "auditd"}]
}

loki.source.file "audit" {
  targets       = local.file_match.audit.targets
  tail_from_end = true
  forward_to    = [loki.write.lab.receiver]
}
```

### 5.16 `.98` and `.100` `/etc/alloy/docker.alloy`
```alloy
// /etc/alloy/docker.alloy  (.98 and .100 only - hosts with Docker)
// Docker uses the json-file log driver, so read via the Docker API.

discovery.docker "local" {
  host = "unix:///var/run/docker.sock"
}

discovery.relabel "docker" {
  targets = []

  rule {
    source_labels = ["__meta_docker_container_name"]
    regex         = "/(.*)"
    target_label  = "container"
  }
  rule {
    source_labels = ["__meta_docker_container_name"]
    regex         = "/(.*)"
    target_label  = "service_name"
  }
  rule {
    source_labels = ["__meta_docker_container_log_stream"]
    target_label  = "stream"
  }
}

loki.source.docker "docker" {
  host          = "unix:///var/run/docker.sock"
  targets       = discovery.docker.local.targets
  relabel_rules = discovery.relabel.docker.rules
  labels        = {job = "docker"}
  forward_to    = [loki.write.lab.receiver]
}
```

### 5.17 `.99` `/etc/alloy/k3s-pods.alloy`
```alloy
// /etc/alloy/k3s-pods.alloy  (.99 only - k3s)
// Reads kubelet pod logs straight from disk (no API access needed):
//   /var/log/pods/<namespace>_<pod>_<uid>/<container>/<restart>.log  (CRI format)

local.file_match "pods" {
  path_targets = [{"__path__" = "/var/log/pods/*/*/*.log"}]
}

discovery.relabel "pods" {
  targets = local.file_match.pods.targets

  rule {
    source_labels = ["__path__"]
    regex         = "/var/log/pods/([^_]+)_([^_]+)_[^/]+/([^/]+)/.*"
    target_label  = "namespace"
    replacement   = "$1"
  }
  rule {
    source_labels = ["__path__"]
    regex         = "/var/log/pods/([^_]+)_([^_]+)_[^/]+/([^/]+)/.*"
    target_label  = "pod"
    replacement   = "$2"
  }
  rule {
    source_labels = ["__path__"]
    regex         = "/var/log/pods/([^_]+)_([^_]+)_[^/]+/([^/]+)/.*"
    target_label  = "container"
    replacement   = "$3"
  }
  rule {
    source_labels = ["container"]
    target_label  = "service_name"
  }
  rule {
    target_label = "job"
    replacement  = "k3s-pods"
  }
}

loki.source.file "pods" {
  targets       = discovery.relabel.pods.output
  tail_from_end = true                // don't replay weeks-old pod logs
  forward_to    = [loki.process.cri.receiver]
}

loki.process "cri" {
  stage.cri {}
  // drop the per-file label; namespace/pod/container already identify the stream
  stage.label_drop {
    values = ["filename"]
  }
  forward_to = [loki.write.lab.receiver]
}
```

### 5.18 All 5 hosts: Alloy service settings
`/etc/systemd/system/alloy.service.d/10-lab.conf`:
```ini
# /etc/systemd/system/alloy.service.d/10-lab.conf
# Run as root: auditd logs (/var/log/audit, 0700) and k3s pod logs
# (/var/log/pods, 0750 root) are root-only, and the Docker socket is
# root-equivalent anyway. Alloy's UI stays on 127.0.0.1:12345 (the default).
[Service]
User=root
Group=root
```
`/etc/sysconfig/alloy` (RPM default, one line changed): `CONFIG_FILE="/etc/alloy"`, `CUSTOM_ARGS=""`.
Unit `/usr/lib/systemd/system/alloy.service` is shipped by the RPM, unmodified.
On Ubuntu (rasp5) the same drop-in is used; settings file is `/etc/default/alloy` (`CONFIG_FILE="/etc/alloy"`),
and the package's sample is renamed `/etc/alloy/config.alloy.pkg-default`. rasp5 has `common.alloy` and `docker.alloy`.

### 5.19 `.98` `/opt/monitoring/grafana/provisioning/plugins/lokiexplore.yml`
```yaml
# /opt/monitoring/grafana/provisioning/plugins/lokiexplore.yml
# Logs Drilldown (grafana-lokiexplore-app): use Loki by default. Without this the
# app falls back to Grafana's default data source (Prometheus) and shows
# "Log volume has not been configured".
apiVersion: 1
apps:
  - type: grafana-lokiexplore-app
    org_id: 1
    disabled: false
    jsonData:
      dataSource: loki
```
Applied without restarting Grafana: `curl -u admin:$PW -X POST localhost:3000/api/admin/provisioning/plugins/reload`.

### 5.20 `.98` `/opt/monitoring/loki/rules/fake/lab-logs.yml` (Loki ruler)
```yaml
# /opt/monitoring/loki/rules/fake/lab-logs.yml   (Loki ruler; tenant "fake" = auth disabled)
# Recording rules are remote-written to Prometheus (lab:log_*), where the
# baseline-relative "LogErrorSpike" / "LogsMissing" alerts live (prometheus/rules/logs.yml).
# Alerting rules here go straight to Alertmanager (localhost:9093).
groups:
  - name: log-recording
    interval: 1m
    rules:
      # all log lines per host/job (used to detect a host that stops shipping)
      - record: lab:log_lines:rate5m
        expr: |
          sum by (host, job) (rate({job=~".+"}[5m]))
      # error-or-worse lines per host/service. detected_level comes from the
      # journal priority for host services and from the line text for containers.
      - record: lab:log_error_lines:rate5m
        expr: |
          sum by (host, service_name) (rate({job=~".+"} | detected_level=~"error|critical|fatal" [5m]))

  - name: log-alerts
    interval: 1m
    rules:
      - alert: LogOOMKill
        expr: |
          sum by (host) (count_over_time({service_name="kernel"} |~ "(?i)out of memory: killed process|oom-kill:" [5m])) > 0
        labels: {severity: critical, source: logs}
        annotations:
          summary: "OOM killer fired on {{ $labels.host }}"
          description: 'Explore: {service_name="kernel", host="{{ $labels.host }}"} |~ "(?i)out of memory|oom-kill"'

      - alert: LogDiskOrFilesystemError
        expr: |
          sum by (host) (count_over_time({service_name="kernel"} |~ "(?i)I/O error|EXT4-fs error|XFS \\(.*\\): (corruption|metadata I/O error)|remounting filesystem read-only|Remounting filesystem read-only" [5m])) > 0
        labels: {severity: critical, source: logs}
        annotations:
          summary: "Kernel disk/filesystem error on {{ $labels.host }}"
          description: 'Explore: {service_name="kernel", host="{{ $labels.host }}"} |~ "(?i)I/O error|fs error|read-only"'

      - alert: LogSegfault
        expr: |
          sum by (host) (count_over_time({service_name="kernel"} |= "segfault at" [5m])) > 0
        labels: {severity: warning, source: logs}
        annotations:
          summary: "Process segfault on {{ $labels.host }}"

      - alert: LogSystemdUnitFailing
        # a unit that failed 3+ times in 15 min (crash loop). One-off failures don't fire.
        expr: |
          sum by (host, failed_unit) (count_over_time({job="journal"} |= "Failed with result" | regexp "(?P<failed_unit>[^ ]+): Failed with result" [15m])) >= 3
        labels: {severity: warning, source: logs}
        annotations:
          summary: "{{ $labels.failed_unit }} on {{ $labels.host }} failed {{ $value }} times in 15m"
          description: 'Explore: {host="{{ $labels.host }}", job="journal"} |= "{{ $labels.failed_unit }}"'

      - alert: LogJournalCritical
        # journald priority crit/alert/emerg from any host service
        expr: |
          sum by (host, service_name) (count_over_time({job="journal", level=~"crit|alert|emerg"}[5m])) > 0
        labels: {severity: critical, source: logs}
        annotations:
          summary: "Critical journal message from {{ $labels.service_name }} on {{ $labels.host }}"

      - alert: LogSSHBruteForce
        expr: |
          sum by (host) (count_over_time({service_name=~"sshd.service|ssh.service"} |~ "Failed password|Invalid user|authentication failure" [5m])) > 20
        labels: {severity: warning, source: logs}
        annotations:
          summary: "{{ $value }} failed SSH logins on {{ $labels.host }} in 5m"
```

### 5.21 `.98` `/opt/monitoring/prometheus/rules/logs.yml`
```yaml
# /opt/monitoring/prometheus/rules/logs.yml
# Alerts on the lab:log_* series that the Loki ruler remote-writes (loki/rules/fake/lab-logs.yml).
groups:
  - name: lab-logs
    rules:
      - alert: LogErrorSpike
        # error rate is 3x this service's own 1-day average AND at least 0.2/s (12/min),
        # sustained 10m. Services with no history count as baseline 0, so a new
        # error source at >=12/min also fires. Per-service baselines keep the
        # crAPI lab's normal error chatter (.101) from firing all day.
        expr: |
          lab:log_error_lines:rate5m > 0.2
          and
          lab:log_error_lines:rate5m
            > 3 * (avg_over_time(lab:log_error_lines:rate5m[1d] offset 15m)
                   or lab:log_error_lines:rate5m * 0)
        for: 10m
        labels: {severity: warning, source: logs}
        annotations:
          summary: "Error spike: {{ $labels.service_name }} on {{ $labels.host }} at {{ $value | printf \"%.2f\" }} errors/s (3x its normal)"
          description: 'Explore: {host="{{ $labels.host }}", service_name="{{ $labels.service_name }}"} | detected_level=~"error|critical|fatal"'

      - alert: LogsMissing
        # host shipped journal logs in the last day but nothing for 20m (Alloy down,
        # host down, or network/firewall to .98:3100 broken)
        expr: |
          max_over_time(lab:log_lines:rate5m{job="journal"}[1d])
            unless on (host) lab:log_lines:rate5m{job="journal"}
        for: 20m
        labels: {severity: warning, source: logs}
        annotations:
          summary: "No logs received from {{ $labels.host }} for 20m+"
```

---

## 6. Firewall rules added, per host

| Host | Mechanism | Rules |
|---|---|---|
| .98 | firewalld zone `public` (runtime + permanent) | 3000, 9090, 9093 ← `192.168.1.0/24`; 9100, 8081 ← `192.168.1.98`; **3100 ← `.99`, `.100`, `.101`, `.102`, `.85`, `.75`–`.78`** (Loki push, added 2026-09-19) |
| .99 | nft table `inet monitoring_acl` via `monitoring-acl.service` | 9100/8081/9882: accept `lo` + `192.168.1.98`, drop everyone else (IPv4 and IPv6) |
| .100 | nft table `inet monitoring_acl` via `monitoring-acl.service` | same as .99 |
| .101 | firewalld zone `public` (runtime + permanent) | 9100, 9882 ← `192.168.1.98` |
| .102 | firewalld zone `public` (runtime + permanent) | 9100, 9882 ← `192.168.1.98` |

Rich rules as listed by `sudo firewall-cmd --permanent --zone=public --list-rich-rules`:

.98:
```
rule family="ipv4" source address="192.168.1.0/24" port port="3000" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.0/24" port port="9090" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.0/24" port port="9093" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.98" port port="9100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.98" port port="8081" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.99" port port="3100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.100" port port="3100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.101" port port="3100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.102" port port="3100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.85" port port="3100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.75" port port="3100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.76" port port="3100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.77" port port="3100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.78" port port="3100" protocol="tcp" accept
```
.101:
```
rule family="ipv4" source address="192.168.1.98" port port="9100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.98" port port="9882" protocol="tcp" accept
```
.102:
```
rule family="ipv4" source address="192.168.1.98" port port="9100" protocol="tcp" accept
rule family="ipv4" source address="192.168.1.98" port port="9882" protocol="tcp" accept
```
No inbound change was needed on .99–.102 or rasp5 for logs: Alloy only makes *outbound* connections to .98:3100,
and its own port 12345 is bound to 127.0.0.1.

Verified: from a non-.98 LAN host (rasp5, .85) every exporter port **times out** (Loki :3100 did too, before rasp5 became a log shipper); from .98 every
one returns **200**; Grafana, Prometheus, and Alertmanager on .98 are reachable from the LAN.

---

## 7. Grafana folder / dashboard structure

Provisioned from files. Each directory under `/opt/monitoring/grafana/dashboards/` is a folder,
and the dashboards are read-only in the UI (edit the generator, not the UI).

```
Fleet Overview/
  ├─ Fleet Overview (all 5 hosts)                 uid fleet-overview      status table + one repeating row per host
  ├─ Node Exporter Full                           uid node-exporter-full  grafana.com #1860 rev 45; pick host in "Host" dropdown
  └─ Monitoring Host (.98): vnotes + stack        uid host-98-containers  cAdvisor: vnotes + prometheus/grafana/alertmanager/cadvisor
k3s Cluster (.99)/
  └─ k3s Cluster (.99): pods & kubelet            uid k3s-cluster-99      kubelet up, pods/containers, per-pod & per-container CPU/mem/net, PLEG
Gateway + CI Host (.100)/
  └─ Gateway + CI Host (.100): Docker + Podman    uid gateway-ci-100      Docker row (jenkins, dind via cAdvisor) + Podman row (kong)
crAPI Stack (.101)/
  └─ crAPI Stack (.101): VULNERABLE-APP SECURITY LAB (not production)
                                                  uid crapi-stack-101     ⚠ banner, 12-container state/health table, CPU/mem/net/IO
MCP Host (.102)/
  └─ MCP Host (.102): vampi-mcp (Podman) + MCP services
                                                  uid mcp-host-102        vampi-mcp panels + systemd up/down tiles for ai-sim/crapi-mcp/noname-mcp
Logs/
  └─ Logs (all hosts)                             uid lab-logs            Loki: host/job/service dropdowns + Search box, log volume by host,
                                                                          errors & warnings by service, log lines
```
Every host dashboard above (except Fleet Overview and Node Exporter Full) also ends with a
**"Logs: errors & warnings"** panel for that host, filtered on `detected_level` (error/warn and worse).

Besides dashboards, Grafana's built-in **Drilldown → Logs** app works on top of Loki: it groups by
`service_name`, and you can filter by `host`. Use **Explore → Loki** for ad-hoc LogQL, e.g.
`{host="hv-rocky-linux-4", container="crapi-identity"} |= "ERROR"` or
`{job="k3s-pods", namespace="vampi"}` or `{job="journal", unit="sshd.service"} |= "Failed password"`.
The **MCP Host** dashboard description and banner flag **future work**: `ai-sim`, `crapi-mcp`, and
`noname-mcp` are systemd Python services that are **not yet instrumented**. Only their systemd state
(node_exporter systemd collector) and host-level resources are visible.

Expected empty panels in Node Exporter Full (46 of 284 queries, the same on every host):
hardware temperature, fans, power supply, and CPU frequency (Hyper-V guests have none);
Pressure Stall Information (PSI is off in the RHEL 9 kernel unless you boot with `psi=1`);
processes, TCP stat, IRQ detail, and systemd sockets (node_exporter collectors that are off by default).

---

## 8. How to add a 6th server

Example: new Rocky host `192.168.1.103` (`hv-rocky-linux-6`). Note that .103/.104 run Rocky **10.2**:
use EPEL 10 for the podman exporter and check it exists there.

1. **Verify ports first** (§9): `ssh 192.168.1.103 'sudo ss -tlnp'`. Confirm 9100 (and 8081/9882 if
   used) are free. If not, pick another free 9xxx port and use it consistently below.
2. **node_exporter:** follow §4.2 using the unit in §5.7 (add that host's key services to
   `--collector.systemd.unit-include` if useful).
3. **Containers (optional):**
   - Docker: cAdvisor as in §4.3 / §5.9 (host network, `--port=8081`).
   - Podman: §4.4 (`prometheus-podman-exporter` on 9882).
4. **Firewall:**
   - firewalld active → rich rules as in §4.5 (source `192.168.1.98`, **no `--reload`**).
   - firewalld inactive and running k8s/containers → nft ACL from §5.10/§5.11.
5. **Prometheus** (.98): add targets to `/opt/monitoring/prometheus/config/prometheus.yml`, e.g.
   ```yaml
   - targets: ["192.168.1.103:9100"]
     labels: {host: hv-rocky-linux-6, role: "<role>"}
   ```
   under `job_name: node` (and under `cadvisor`/`podman` if applicable). Then:
   ```bash
   # promtool check (§4.7), then
   curl -X POST localhost:9090/-/reload
   ```
6. **Grafana:** the Fleet Overview and Node Exporter Full dashboards pick the new `host` up
   automatically. For a dedicated dashboard, add a block to `gen_dashboards.py`
   (copy the `.102` block), regenerate, and redeploy per §4.8.
7. **Logs:** install Alloy per §4.10 with `common.alloy` (plus `docker.alloy` if it runs Docker). On .98 add
   a rich rule `add 192.168.1.103 3100`. Podman containers are covered by the journal automatically.
   On Rocky 10, confirm Podman's log driver is still `journald` (`sudo podman info --format '{{.Host.LogDriver}}'`);
   if it's `k8s-file`, add a file source for `/var/lib/containers/storage/overlay-containers/*/userdata/ctr.log`.
   The Logs dashboard picks the new `host` up automatically.
8. **Validate:** the target is `UP` at http://192.168.1.98:9090/targets; run `validate_grafana.py`.
9. Update this document's host table and port tables.

---

## 9. How to re-verify a live host before future changes

> **.99–.102 all run real or lab workloads** (k3s + cloudflared tunnel, kong/jenkins, the crAPI
> vulnerable-app lab, MCP servers). The Sep 18 inventory was produced by a **rescan**, and so was this
> setup: before touching any of these hosts, spot-check it again. **Don't trust this document's
> port tables blindly.**

Before any change on a host:
```bash
# 1. what's listening right now (compare against §2)
ssh 192.168.1.<N> 'sudo ss -tlnp'
# 2. container engines (published ports vs. container-internal ports)
ssh 192.168.1.<N> 'sudo podman ps -a --format "{{.Names}}|{{.Status}}|{{.Ports}}"; sudo docker ps -a --format "{{.Names}}|{{.Status}}|{{.Ports}}" 2>/dev/null'
# 3. firewall mode (determines firewalld rich rules vs nft ACL)
ssh 192.168.1.<N> 'systemctl is-active firewalld; sudo firewall-cmd --list-rich-rules 2>/dev/null; sudo nft list table inet monitoring_acl 2>/dev/null'
# 4. .99 only: remember full paths under sudo
ssh 192.168.1.99 'sudo /usr/local/bin/k3s kubectl get pods -A; systemctl is-active k3s cloudflared'
# 5. monitoring itself: every target still UP?
ssh 192.168.1.98 'curl -s localhost:9090/api/v1/targets' | grep -o '"health":"[a-z]*"' | sort | uniq -c
```

Things the 2026-09-18 re-verification found (deltas vs. the inventory):
- **.101:** only 8 of the 14 "in use" ports are published on the host (3000, 5500, 8025, 8080, 8443,
  8888, 30080, 30443). **8989, 10001, 6060, 5002, 443, and 1025 are container-internal only.** All 12
  containers were up. All 14 are still treated as reserved.
- **.100:** kong's 8000 is its *container* port (host 80→8000); there's no host listener on 8000.
- **.99:** NodePorts 30300/30453/30500 have no listening socket (kube-proxy iptables), so `ss` won't
  show them; they're still in use. Extra localhost-only ports 10245–10247 (ingress-nginx) and 631 (cups).
- **All hosts:** 127.0.0.1:631 (cups) and, on .98, 127.0.0.1:44321 (opencode): localhost only.
- vampi on k3s is **one pod with 2 containers** (app + noname-security-sensor), which is what "2/2" means.

Hard "do not touch" list (unchanged by this setup; verify after any change):
- .99: k3s service, all Deployments/Helm releases, the vampi eBPF sidecar, cloudflared. Only additions:
  node_exporter, `monitoring-acl`, and the `monitoring` namespace RBAC.
- .100: kong (Podman), jenkins + jenkins-docker (Docker) and dind certs.
- .101: the 12 crAPI-stack containers; `noname-sensor` stays **inactive + disabled**.
- .102: ai-sim, crapi-mcp, noname-mcp, vampi-mcp; `noname-sensor` stays **inactive + disabled**.
- Never `firewall-cmd --reload` on .98/.101/.102 without planning to re-check container networking afterward.
- Never enable firewalld or `nftables.service` on .99/.100.
- Logging added only: the Alloy RPM + `/etc/alloy/*.alloy` + the systemd drop-in on each host, and on .98 the
  `loki` compose service, `/opt/monitoring/loki/`, the Grafana `loki.yml` data source, and 4 rich rules for :3100.
  No journald, rsyslog, Docker or Podman log-driver setting was changed.

---

## 10. Files in this directory

| Path | Purpose |
|---|---|
| `MONITORING_SETUP.md` | this document |
| `monitoring/gen_dashboards.py` | generates all Grafana dashboard JSON (folders = subdirectories), incl. `Logs/` and per-host log panels |
| `monitoring/1860.json` | Node Exporter Full, grafana.com #1860 rev 45 (input to the generator) |
| `monitoring/validate_grafana.py` | runs every dashboard panel query (Prometheus **and Loki**) through Grafana and reports empty ones |
| `monitoring/prometheus-scraper-rbac.yaml` | k3s RBAC for the kubelet scrape |
| `monitoring/loki/loki.yml` | Loki config (§5.13), deployed to `/opt/monitoring/loki/config/` on .98 |
| `monitoring/loki/grafana-datasource-loki.yml` | Grafana Loki data source (§5.14) |
| `monitoring/alloy/common.alloy` | Alloy config for all 5 hosts: journal + auditd (§5.15) |
| `monitoring/alloy/docker.alloy` | Alloy Docker logs, .98/.100 (§5.16) |
| `monitoring/alloy/k3s-pods.alloy` | Alloy k3s pod logs, .99 (§5.17) |
| `monitoring/alloy/alloy-root.conf` | systemd drop-in → `/etc/systemd/system/alloy.service.d/10-lab.conf` (§5.18) |
| `monitoring/alloy/install-alloy-ubuntu.sh` | installs/configures Alloy on Ubuntu hosts (rasp5 done; rk1–rk4 pending), §4.12 |
| `monitoring/loki/grafana-plugin-lokiexplore.yml` | makes Loki the Logs Drilldown default (§5.19) |
| `monitoring/loki/rules/lab-logs.yml` | Loki ruler: log alerts + `lab:log_*` recording rules (§5.20) |
| `monitoring/loki/prometheus-rules-logs.yml` | Prometheus `LogErrorSpike` / `LogsMissing` (§5.21) |

Backups made on .98 on 2026-09-19 before the logging change: `/opt/monitoring/docker-compose.yml.bak-20260919`,
`/opt/monitoring/prometheus/prometheus.yml.bak-20260919`. Before the log-alerts change:
`/opt/monitoring/docker-compose.yml.bak-20260919b`, `/opt/monitoring/loki/loki.yml.bak-20260919`.
