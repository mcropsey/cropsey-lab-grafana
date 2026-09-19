#!/usr/bin/env python3
"""Generate the lab's Grafana dashboards as provisioning JSON.

Output layout (each top-level dir = one Grafana folder):
  out/Fleet Overview/            node-exporter-full.json, fleet-overview.json
  out/k3s Cluster (.99)/         k3s-cluster.json
  out/Gateway + CI Host (.100)/  gateway-ci.json
  out/crAPI Stack (.101)/        crapi-stack.json
  out/MCP Host (.102)/           mcp-host.json
  out/Logs/                      logs.json            (Loki, every host running Alloy)

Every host dashboard also gets a "Logs: errors & warnings" panel at the bottom.
"""
import json, os, sys, copy

OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
NEF = sys.argv[2] if len(sys.argv) > 2 else "1860.json"
DS = {"type": "prometheus", "uid": "prometheus"}
LDS = {"type": "loki", "uid": "loki"}

_id = [0]
def nid():
    _id[0] += 1
    return _id[0]

def ts(title, exprs, unit="short", x=0, y=0, w=12, h=8, desc="", stack=False, legend="{{name}}",
       ds=DS, bars=False):
    if isinstance(exprs, str):
        exprs = [(exprs, legend)]
    return {
        "id": nid(), "type": "timeseries", "title": title, "description": desc,
        "datasource": ds, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit, "custom": {
            "drawStyle": "bars" if bars else "line", "fillOpacity": 80 if bars else 10,
            "lineWidth": 1, "showPoints": "never",
            "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "right",
                               "calcs": ["lastNotNull", "max"]},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": [{"refId": chr(65 + i), "datasource": ds, "expr": e, "legendFormat": l}
                    for i, (e, l) in enumerate(exprs)],
    }

def logs(title, expr, x=0, y=0, w=24, h=14, desc=""):
    return {
        "id": nid(), "type": "logs", "title": title, "description": desc,
        "datasource": LDS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True,
                    "sortOrder": "Descending", "dedupStrategy": "none", "prettifyLogMessage": False},
        "targets": [{"refId": "A", "datasource": LDS, "expr": expr, "queryType": "range"}],
    }

# errors/warnings: journal priority for host services, detected_level (from the
# line text) for container/pod output
ERR_FILTER = '| detected_level=~"(?i)(emerg|alert|crit|critical|fatal|err|error|warn|warning)"'

def host_logs(host, panels, extra=""):
    """Append an errors/warnings log panel under a host dashboard's last panel."""
    y = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in panels)
    panels.append(logs("Logs: errors & warnings",
                       f'{{host="{host}"{extra}}} {ERR_FILTER}', 0, y, 24, 12,
                       desc="From Loki. Open the Logs dashboard (Logs folder) or Drilldown > Logs for everything."))

def stat(title, expr, unit="short", x=0, y=0, w=4, h=4, desc="", mappings=None,
         thresholds=None, legend="", color_mode="value"):
    return {
        "id": nid(), "type": "stat", "title": title, "description": desc,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {
            "unit": unit, "mappings": mappings or [],
            "thresholds": {"mode": "absolute", "steps": thresholds or
                           [{"color": "green", "value": None}]}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": color_mode,
                    "graphMode": "none", "textMode": "auto", "justifyMode": "auto"},
        "targets": [{"refId": "A", "datasource": DS, "expr": expr, "legendFormat": legend,
                     "instant": True}],
    }

def table(title, targets, x=0, y=0, w=24, h=8, desc="", overrides=None, organize=None):
    t = {
        "id": nid(), "type": "table", "title": title, "description": desc,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": overrides or []},
        "options": {"showHeader": True, "cellHeight": "sm"},
        "targets": [{"refId": chr(65 + i), "datasource": DS, "expr": e, "format": "table",
                     "instant": True} for i, e in enumerate(targets)],
        "transformations": [{"id": "merge", "options": {}}],
    }
    if organize:
        t["transformations"].append({"id": "organize", "options": organize})
    return t

def text(title, md, x=0, y=0, w=24, h=4):
    return {"id": nid(), "type": "text", "title": title,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"mode": "markdown", "content": md}}

def row(title, y, repeat=None):
    r = {"id": nid(), "type": "row", "title": title, "collapsed": False,
         "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}
    if repeat:
        r["repeat"] = repeat
    return r

def dash(uid, title, panels, desc="", tags=(), templating=(), refresh="30s"):
    _id[0] = 0
    return {"uid": uid, "title": title, "description": desc, "tags": list(tags),
            "editable": False, "schemaVersion": 39, "version": 1, "refresh": refresh,
            "time": {"from": "now-6h", "to": "now"}, "timezone": "browser",
            "templating": {"list": list(templating)}, "annotations": {"list": []},
            "panels": panels}

def qvar(name, query, label=None, multi=True, include_all=True, all_selected=True, hide=0, ds=DS):
    v = {"name": name, "label": label or name, "type": "query", "datasource": ds,
         "query": {"query": query, "refId": "v"}, "definition": query, "refresh": 2,
         "multi": multi, "includeAll": include_all, "sort": 1, "hide": hide}
    if all_selected:
        v["current"] = {"selected": True, "text": ["All"], "value": ["$__all"]}
    return v

PODMAN_STATE = [{"type": "value", "options": {
    "-1": {"text": "unknown", "color": "gray"}, "0": {"text": "created", "color": "blue"},
    "1": {"text": "initialized", "color": "blue"}, "2": {"text": "running", "color": "green"},
    "3": {"text": "stopped", "color": "red"}, "4": {"text": "paused", "color": "orange"},
    "5": {"text": "exited", "color": "red"}, "6": {"text": "removing", "color": "orange"},
    "7": {"text": "stopping", "color": "orange"}}}]
PODMAN_HEALTH = [{"type": "value", "options": {
    "-1": {"text": "no healthcheck", "color": "gray"}, "0": {"text": "healthy", "color": "green"},
    "1": {"text": "unhealthy", "color": "red"}, "2": {"text": "starting", "color": "orange"}}}]
UPDOWN = [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"},
                                         "1": {"text": "UP", "color": "green"}}}]
RED0 = [{"color": "red", "value": None}, {"color": "green", "value": 1}]

def host_summary(host, y):
    """4 stat tiles for a host from node_exporter."""
    h = f'host="{host}"'
    return [
        stat("Uptime", f'time() - node_boot_time_seconds{{{h}}}', "s", 0, y, 4, 3),
        stat("CPU busy", f'100 * (1 - avg(rate(node_cpu_seconds_total{{{h},mode="idle"}}[5m])))',
             "percent", 4, y, 4, 3, thresholds=[{"color": "green", "value": None},
                                                {"color": "orange", "value": 70},
                                                {"color": "red", "value": 90}]),
        stat("Memory used", f'100 * (1 - node_memory_MemAvailable_bytes{{{h}}} / node_memory_MemTotal_bytes{{{h}}})',
             "percent", 8, y, 4, 3, thresholds=[{"color": "green", "value": None},
                                                {"color": "orange", "value": 80},
                                                {"color": "red", "value": 95}]),
        stat("Root FS used", f'100 * (1 - node_filesystem_avail_bytes{{{h},mountpoint="/"}} / node_filesystem_size_bytes{{{h},mountpoint="/"}})',
             "percent", 12, y, 4, 3, thresholds=[{"color": "green", "value": None},
                                                 {"color": "orange", "value": 80},
                                                 {"color": "red", "value": 90}]),
        stat("Load 5m / cores", f'node_load5{{{h}}} / scalar(count(node_cpu_seconds_total{{{h},mode="idle"}}))',
             "percentunit", 16, y, 4, 3),
        stat("node_exporter", f'up{{job="node",{h}}}', "none", 20, y, 4, 3,
             mappings=UPDOWN, thresholds=RED0, color_mode="background"),
    ]

def podman_panels(host, y, name_re=".+"):
    sel = f'job="podman",host="{host}",name=~"{name_re}"'
    return [
        table("Containers (Podman)", [
            f'podman_container_state{{{sel}}}',
            f'podman_container_health{{{sel}}}',
            f'time() - podman_container_started_seconds{{{sel}}}',
            f'podman_container_mem_usage_bytes{{{sel}}}',
        ], 0, y, 24, 7, organize={
            "excludeByName": {"Time": True, "__name__": True, "job": True, "instance": True,
                              "host": True, "id": True, "image_id": True, "pod_id": True,
                              "environment": True},
            "renameByName": {"Value #A": "state", "Value #B": "health",
                             "Value #C": "up for", "Value #D": "memory"},
            "indexByName": {"name": 0, "Value #A": 1, "Value #B": 2, "Value #C": 3,
                            "Value #D": 4, "image": 5, "pod_name": 6, "ports": 7}},
            overrides=[
                {"matcher": {"id": "byName", "options": "state"}, "properties": [
                    {"id": "mappings", "value": PODMAN_STATE},
                    {"id": "custom.cellOptions", "value": {"type": "color-background"}}]},
                {"matcher": {"id": "byName", "options": "health"}, "properties": [
                    {"id": "mappings", "value": PODMAN_HEALTH},
                    {"id": "custom.cellOptions", "value": {"type": "color-background"}}]},
                {"matcher": {"id": "byName", "options": "up for"}, "properties": [{"id": "unit", "value": "s"}]},
                {"matcher": {"id": "byName", "options": "memory"}, "properties": [{"id": "unit", "value": "bytes"}]},
            ]),
        ts("Podman CPU (cores)", f'rate(podman_container_cpu_seconds_total{{{sel}}}[5m])',
           "short", 0, y + 7, 12, 8),
        ts("Podman memory", f'podman_container_mem_usage_bytes{{{sel}}}', "bytes", 12, y + 7, 12, 8),
        ts("Podman network", [(f'rate(podman_container_net_input_total{{{sel}}}[5m])', "{{name}} rx"),
                              (f'-rate(podman_container_net_output_total{{{sel}}}[5m])', "{{name}} tx")],
           "Bps", 0, y + 15, 12, 8, desc="rx positive, tx negative. Containers in the same pod share a netns and report the same totals."),
        ts("Podman block I/O", [(f'rate(podman_container_block_input_total{{{sel}}}[5m])', "{{name}} read"),
                                (f'-rate(podman_container_block_output_total{{{sel}}}[5m])', "{{name}} write")],
           "Bps", 12, y + 15, 12, 8),
    ]

def cadvisor_panels(host, y, name_re=".+"):
    sel = f'job="cadvisor",host="{host}",name=~"{name_re}"'
    return [
        ts("Docker CPU (cores)", f'sum by (name) (rate(container_cpu_usage_seconds_total{{{sel}}}[5m]))',
           "short", 0, y, 12, 8),
        ts("Docker memory (working set)", f'container_memory_working_set_bytes{{{sel}}}', "bytes", 12, y, 12, 8),
        ts("Docker network", [(f'sum by (name) (rate(container_network_receive_bytes_total{{{sel}}}[5m]))', "{{name}} rx"),
                              (f'-sum by (name) (rate(container_network_transmit_bytes_total{{{sel}}}[5m]))', "{{name}} tx")],
           "Bps", 0, y + 8, 12, 8),
        ts("Docker block I/O", [(f'sum by (name) (rate(container_fs_reads_bytes_total{{{sel}}}[5m]))', "{{name}} read"),
                                (f'-sum by (name) (rate(container_fs_writes_bytes_total{{{sel}}}[5m]))', "{{name}} write")],
           "Bps", 12, y + 8, 12, 8),
    ]

dashboards = {}

# ---------------------------------------------------------------- Fleet Overview
p = [text("", "**Fleet Overview**: one row per host (`host` variable, All = every host). "
               "For deep per-host detail open **Node Exporter Full** in this folder.", h=2)]
y = 2
p.append(table("Fleet status", [
    'max by (host,instance,role) (up{job="node"})',
    'max by (host,instance,role) (100 * (1 - avg by (host,instance,role) (rate(node_cpu_seconds_total{mode="idle"}[5m]))))',
    'max by (host,instance,role) (100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))',
    'max by (host,instance,role) (100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}))',
    'max by (host,instance,role) (time() - node_boot_time_seconds)',
], 0, y, 24, 7, organize={
    "excludeByName": {"Time": True, "__name__": True, "job": True, "device": True,
                      "fstype": True, "mountpoint": True},
    "renameByName": {"Value #A": "exporter", "Value #B": "CPU %", "Value #C": "Mem %",
                     "Value #D": "Root FS %", "Value #E": "uptime"},
    "indexByName": {"host": 0, "instance": 1, "role": 2, "Value #A": 3, "Value #B": 4,
                    "Value #C": 5, "Value #D": 6, "Value #E": 7}},
    overrides=[
        {"matcher": {"id": "byName", "options": "exporter"}, "properties": [
            {"id": "mappings", "value": UPDOWN},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}}]},
        {"matcher": {"id": "byRegexp", "options": ".*%"}, "properties": [
            {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 1},
            {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic"}},
            {"id": "max", "value": 100}, {"id": "min", "value": 0}]},
        {"matcher": {"id": "byName", "options": "uptime"}, "properties": [{"id": "unit", "value": "s"}]},
    ]))
y += 7
r = row("$host", y, repeat="host")
p.append(r); y += 1
H = 'host="$host"'
p += [
    ts("CPU by mode", f'sum by (mode) (rate(node_cpu_seconds_total{{{H},mode!="idle"}}[5m])) / scalar(count(node_cpu_seconds_total{{{H},mode="idle"}}))',
       "percentunit", 0, y, 6, 7, stack=True, legend="{{mode}}"),
    ts("Memory", [(f'node_memory_MemTotal_bytes{{{H}}} - node_memory_MemAvailable_bytes{{{H}}}', "used"),
                  (f'node_memory_MemTotal_bytes{{{H}}}', "total")], "bytes", 6, y, 6, 7),
    ts("Network (eth0)", [(f'rate(node_network_receive_bytes_total{{{H},device="eth0"}}[5m])', "rx"),
                          (f'-rate(node_network_transmit_bytes_total{{{H},device="eth0"}}[5m])', "tx")],
       "Bps", 12, y, 6, 7),
    ts("Disk I/O", [(f'sum(rate(node_disk_read_bytes_total{{{H}}}[5m]))', "read"),
                    (f'-sum(rate(node_disk_written_bytes_total{{{H}}}[5m]))', "write")],
       "Bps", 18, y, 6, 7),
]
dashboards["Fleet Overview/fleet-overview.json"] = dash(
    "fleet-overview", "Fleet Overview (all 5 hosts)", p,
    "Summary of all 5 Rocky Linux hosts from node_exporter, one repeating row per host.",
    ["fleet", "node_exporter"],
    [qvar("host", 'label_values(up{job="node"}, host)')])

# vnotes / monitoring-stack containers on .98 (cAdvisor) — lives with the fleet
p = [text("", "Docker containers on **.98 hv-rocky-linux-1** via cAdvisor :8081: the `vnotes` "
               "app plus the monitoring stack itself (prometheus, grafana, alertmanager, cadvisor).", h=2)]
p += host_summary("hv-rocky-linux-1", 2)
p += cadvisor_panels("hv-rocky-linux-1", 5)
host_logs("hv-rocky-linux-1", p)
dashboards["Fleet Overview/monitoring-host-98.json"] = dash(
    "host-98-containers", "Monitoring Host (.98): vnotes + stack containers", p,
    "cAdvisor view of Docker containers on 192.168.1.98.", ["docker", "cadvisor", "98"])

# Node Exporter Full (grafana.com 1860), pinned to our datasource + job
nef = json.load(open(NEF))
nef.pop("__inputs", None); nef.pop("__requires", None); nef.pop("id", None)
nef["uid"] = "node-exporter-full"
for v in nef["templating"]["list"]:
    if v["name"] == "ds_prometheus":
        v["current"] = {"selected": True, "text": "Prometheus", "value": "prometheus"}
    if v["name"] == "job":
        v["current"] = {"selected": True, "text": "node", "value": "node"}
dashboards["Fleet Overview/node-exporter-full.json"] = nef

# ---------------------------------------------------------------- k3s (.99)
K = 'job="k3s-cadvisor",namespace=~"$namespace"'
KC = K + ',container!="",container!="POD"'
p = [text("", "Single-node **k3s v1.36** on **.99 hv-rocky-linux-2**. Data is scraped straight from the "
               "kubelet at `https://192.168.1.99:10250` (`/metrics` + `/metrics/cadvisor`) using a "
               "read-only ServiceAccount token. **kube-state-metrics is not deployed**, so "
               "Deployment/replica/restart-count state isn't available; pod-level resource usage is.", h=3)]
y = 3
p += [
    stat("kubelet", 'up{job="k3s-kubelet"}', "none", 0, y, 3, 4, mappings=UPDOWN, thresholds=RED0, color_mode="background"),
    stat("kubelet cAdvisor", 'up{job="k3s-cadvisor"}', "none", 3, y, 3, 4, mappings=UPDOWN, thresholds=RED0, color_mode="background"),
    stat("Running pods", 'kubelet_running_pods{job="k3s-kubelet"}', "none", 6, y, 3, 4),
    stat("Running containers", 'sum(kubelet_running_containers{job="k3s-kubelet",container_state="running"})', "none", 9, y, 3, 4),
    stat("Node CPU busy", '100 * (1 - avg(rate(node_cpu_seconds_total{host="hv-rocky-linux-2",mode="idle"}[5m])))', "percent", 12, y, 4, 4),
    stat("Node memory used", '100 * (1 - node_memory_MemAvailable_bytes{host="hv-rocky-linux-2"} / node_memory_MemTotal_bytes{host="hv-rocky-linux-2"})', "percent", 16, y, 4, 4),
    stat("k3s.service", 'node_systemd_unit_state{host="hv-rocky-linux-2",name="k3s.service",state="active"}', "none", 20, y, 4, 4, mappings=UPDOWN, thresholds=RED0, color_mode="background"),
]
y += 4
p.append(table("Pods", [
    f'sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{{{KC}}}[5m]))',
    f'sum by (namespace,pod) (container_memory_working_set_bytes{{{KC}}})',
    f'count by (namespace,pod) (container_memory_working_set_bytes{{{KC}}})',
], 0, y, 24, 8, organize={
    "excludeByName": {"Time": True},
    "renameByName": {"Value #A": "CPU (cores)", "Value #B": "memory", "Value #C": "containers"}},
    overrides=[{"matcher": {"id": "byName", "options": "memory"}, "properties": [{"id": "unit", "value": "bytes"}]},
               {"matcher": {"id": "byName", "options": "CPU (cores)"}, "properties": [{"id": "decimals", "value": 4}]}]))
y += 8
p += [
    ts("CPU by pod (cores)", f'sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{{{KC}}}[5m]))',
       "short", 0, y, 12, 8, legend="{{namespace}}/{{pod}}"),
    ts("Memory working set by pod", f'sum by (namespace,pod) (container_memory_working_set_bytes{{{KC}}})',
       "bytes", 12, y, 12, 8, legend="{{namespace}}/{{pod}}"),
    ts("CPU by container (cores)", f'sum by (namespace,pod,container) (rate(container_cpu_usage_seconds_total{{{KC}}}[5m]))',
       "short", 0, y + 8, 12, 8, legend="{{namespace}}/{{pod}}/{{container}}",
       desc="Shows the vampi app container and its noname-security-sensor sidecar separately."),
    ts("CPU throttling", f'sum by (namespace,pod) (rate(container_cpu_cfs_throttled_seconds_total{{{KC}}}[5m]))',
       "s", 12, y + 8, 12, 8, legend="{{namespace}}/{{pod}}"),
    ts("Network by pod", [(f'sum by (namespace,pod) (rate(container_network_receive_bytes_total{{{K}}}[5m]))', "{{namespace}}/{{pod}} rx"),
                          (f'-sum by (namespace,pod) (rate(container_network_transmit_bytes_total{{{K}}}[5m]))', "{{namespace}}/{{pod}} tx")],
       "Bps", 0, y + 16, 12, 8, desc="ingress-nginx is hostNetwork so its traffic shows on the node, not here."),
    ts("Kubelet API/PLEG health", [
        ('histogram_quantile(0.99, sum by (le) (rate(kubelet_pleg_relist_duration_seconds_bucket{job="k3s-kubelet"}[5m])))', "PLEG relist p99"),
        ('histogram_quantile(0.99, sum by (le) (rate(kubelet_runtime_operations_duration_seconds_bucket{job="k3s-kubelet"}[5m])))', "runtime ops p99")],
       "s", 12, y + 16, 12, 8),
]
host_logs("hv-rocky-linux-2", p)
dashboards["k3s Cluster (.99)/k3s-cluster.json"] = dash(
    "k3s-cluster-99", "k3s Cluster (.99): pods & kubelet", p,
    "k3s single node on 192.168.1.99, fed by a direct kubelet scrape (no kube-prometheus-stack). "
    "Auth: SA monitoring/prometheus-scraper, TLS verified with k3s server CA.",
    ["k3s", "kubernetes", "99"],
    [qvar("namespace", 'label_values(container_cpu_usage_seconds_total{job="k3s-cadvisor",namespace!=""}, namespace)')])

# ---------------------------------------------------------------- .100
p = [text("", "**.100 hv-rocky-linux-3**: API gateway + CI. Runs **both** engines: "
               "**Docker** (`jenkins`, `jenkins-docker` dind) via cAdvisor :8081 and **Podman** "
               "(`kong` 3.6) via prometheus-podman-exporter :9882. Containers started *inside* "
               "dind by Jenkins builds are nested and are **not** broken out individually; they "
               "count toward `jenkins-docker`.", h=3)]
p += host_summary("hv-rocky-linux-3", 3)
p.append(row("Docker: Jenkins + dind (cAdvisor)", 6))
p += cadvisor_panels("hv-rocky-linux-3", 7)
p.append(row("Podman: Kong gateway", 23))
p += podman_panels("hv-rocky-linux-3", 24)
host_logs("hv-rocky-linux-3", p)
dashboards["Gateway + CI Host (.100)/gateway-ci.json"] = dash(
    "gateway-ci-100", "Gateway + CI Host (.100): Docker + Podman", p,
    "Combined Docker (cAdvisor: jenkins, jenkins-docker) and Podman (kong) view of 192.168.1.100.",
    ["docker", "podman", "kong", "jenkins", "100"])

# ---------------------------------------------------------------- .101
p = [text("", "## ⚠️ VULNERABLE-APP SECURITY LAB, NOT PRODUCTION\n"
               "**.101 hv-rocky-linux-4** runs the intentionally-vulnerable **OWASP crAPI** stack (+ juice-shop, searxng) "
               "as 12 rootful Podman containers. Anomalous traffic, errors and resource spikes here are often "
               "*expected* (attack exercises). Monitoring is **read-only**: nothing here restarts or reconfigures containers. "
               "The `noname-sensor` unit is intentionally inactive.", h=4)]
p += host_summary("hv-rocky-linux-4", 4)
p += [
    stat("Containers running", 'count(podman_container_state{host="hv-rocky-linux-4"} == 2)', "none", 0, 7, 6, 3,
         thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 11}, {"color": "green", "value": 12}],
         desc="Expected: 12"),
    stat("Unhealthy containers", 'count(podman_container_health{host="hv-rocky-linux-4"} == 1) or vector(0)', "none", 6, 7, 6, 3,
         thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]),
    stat("podman exporter", 'up{job="podman",host="hv-rocky-linux-4"}', "none", 12, 7, 6, 3,
         mappings=UPDOWN, thresholds=RED0, color_mode="background"),
    stat("noname-sensor.service (should be inactive)", 'node_systemd_unit_state{host="hv-rocky-linux-4",name="noname-sensor.service",state="active"} or vector(0)',
         "none", 18, 7, 6, 3, mappings=[{"type": "value", "options": {"0": {"text": "inactive (expected)", "color": "green"},
                                                                     "1": {"text": "ACTIVE", "color": "orange"}}}],
         color_mode="background"),
]
p += podman_panels("hv-rocky-linux-4", 10, "$container")
host_logs("hv-rocky-linux-4", p)
dashboards["crAPI Stack (.101)/crapi-stack.json"] = dash(
    "crapi-stack-101", "crAPI Stack (.101): VULNERABLE-APP SECURITY LAB (not production)", p,
    "Intentionally-vulnerable security lab on 192.168.1.101 (OWASP crAPI + juice-shop + searxng, 12 Podman containers). NOT production.",
    ["podman", "crapi", "security-lab", "not-production", "101"],
    [qvar("container", 'label_values(podman_container_info{host="hv-rocky-linux-4"}, name)', label="container")])

# ---------------------------------------------------------------- .102
MCP_DESC = ("MCP servers + AI simulator on 192.168.1.102. Podman container vampi-mcp is fully instrumented. "
            "FUTURE WORK: ai-sim (:8011/:8012), crapi-mcp (:8009) and noname-mcp (:8013) are systemd Python "
            "services that are NOT yet instrumented; only their systemd up/down state (node_exporter "
            "systemd collector) and host-level resources are visible. Add a /metrics endpoint "
            "(e.g. prometheus_client) to each to get request/latency/error metrics.")
p = [text("", "**.102 hv-rocky-linux-5**: MCP servers + AI simulator.\n\n"
               "> 🚩 **Future work:** `ai-sim` (:8011 LLM, :8012 GenAI), `crapi-mcp` (:8009) and `noname-mcp` (:8013) "
               "are systemd Python services **not yet instrumented**. Below you only get their systemd state and "
               "per-service CPU/memory isn't broken out. `vampi-mcp` (Podman, :5000) is fully covered.", h=4)]
p += host_summary("hv-rocky-linux-5", 4)
for i, svc in enumerate(["ai-sim", "crapi-mcp", "noname-mcp", "noname-sensor"]):
    exp = "inactive (expected)" if svc == "noname-sensor" else "UP"
    p.append(stat(f"{svc}.service", f'node_systemd_unit_state{{host="hv-rocky-linux-5",name="{svc}.service",state="active"}} or vector(0)',
                  "none", i * 6, 7, 6, 3, color_mode="background",
                  mappings=[{"type": "value", "options": {
                      "0": {"text": "inactive (expected)" if svc == "noname-sensor" else "DOWN",
                            "color": "green" if svc == "noname-sensor" else "red"},
                      "1": {"text": "ACTIVE (unexpected)" if svc == "noname-sensor" else "UP",
                            "color": "orange" if svc == "noname-sensor" else "green"}}}]))
p.append(row("Podman: vampi-mcp", 10))
p += podman_panels("hv-rocky-linux-5", 11)
host_logs("hv-rocky-linux-5", p)
dashboards["MCP Host (.102)/mcp-host.json"] = dash(
    "mcp-host-102", "MCP Host (.102): vampi-mcp (Podman) + MCP services", p, MCP_DESC,
    ["podman", "mcp", "102", "todo-instrument"])

# ---------------------------------------------------------------- Logs (Loki)
L = 'host=~"$host", job=~"$job", service_name=~"$service"'
p = [text("", "Logs via Grafana Alloy → Loki (.98:3100) from the 5 Rocky hosts (.98–.102), **rasp5** (.85) and, once installed, "
               "**rk1–rk4**. `job`: **journal** (systemd journal: system services, sshd/sudo, and every **Podman** container), "
               "**docker** (.98/.100/rasp5), **k3s-pods** (.99), **audit** (auditd, Rocky only). Type in *Search* to filter lines (regex, case-insensitive). "
               "For ad-hoc browsing use **Drilldown → Logs** or **Explore** with the Loki data source.", h=3)]
p += [
    ts("Log volume by host", f'sum by (host) (count_over_time({{{L}}} |~ "(?i)$search" [$__auto]))',
       "short", 0, 3, 12, 7, stack=True, legend="{{host}}", ds=LDS, bars=True),
    ts("Errors & warnings by service", f'sum by (host, service_name) (count_over_time({{{L}}} |~ "(?i)$search" {ERR_FILTER} [$__auto]))',
       "short", 12, 3, 12, 7, stack=True, legend="{{host}} {{service_name}}", ds=LDS, bars=True),
    logs("Logs", f'{{{L}}} |~ "(?i)$search"', 0, 10, 24, 20),
]
search = {"name": "search", "label": "Search", "type": "textbox", "query": "",
          "current": {"text": "", "value": ""}}
dashboards["Logs/logs.json"] = dash(
    "lab-logs", "Logs (all hosts)", p,
    "Loki logs for the Rocky hosts, rasp5 and rk1-rk4: journal (incl. Podman), Docker, k3s pods, auditd.",
    ["logs", "loki"],
    [qvar("host", 'label_values(host)', ds=LDS),
     qvar("job", 'label_values({host=~"$host"}, job)', ds=LDS),
     qvar("service", 'label_values({host=~"$host", job=~"$job"}, service_name)', label="service", ds=LDS),
     search], refresh="1m")

for path, d in dashboards.items():
    full = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump(d, f, indent=1)
    print(full)
