#!/usr/bin/env python3
"""Run every panel query of every provisioned dashboard through Grafana's /api/ds/query
(Prometheus and Loki) and report panels that return no data. Usage: validate_grafana.py <grafana_url> <pwfile>"""
import json, sys, base64, urllib.request, re, time
G, PW = sys.argv[1], open(sys.argv[2]).read().strip()
AUTH = "Basic " + base64.b64encode(f"admin:{PW}".encode()).decode()
def api(path, body=None):
    req = urllib.request.Request(G + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": AUTH, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))
HOSTS = {"hv-rocky-linux-%d" % i: "192.168.1.%d:9100" % (97 + i) for i in range(1, 6)}
def subst(expr, extra):
    for k, v in extra.items():
        expr = expr.replace("$" + k, v).replace("${%s}" % k, v)
    # repeat-row panels use exact matches (host="$host"); make them regex so ".+" works
    expr = re.sub(r'="\$\{?(namespace|container|host|job|service)\}?"', '=~".+"', expr)
    expr = re.sub(r"\$\{?(namespace|container|host|job|service)\}?", ".+", expr)
    expr = expr.replace("$search", "").replace("$__auto", "1m")
    expr = expr.replace("$__rate_interval", "5m").replace("$__interval", "1m").replace("${__range}", "1h")
    return expr
def run(expr, ds="prometheus"):
    now = int(time.time() * 1000)
    r = api("/api/ds/query", {"from": str(now - 900_000), "to": str(now), "queries": [{
        "refId": "A", "datasource": {"uid": ds}, "expr": expr, "range": True, "queryType": "range",
        "instant": False, "intervalMs": 30000, "maxDataPoints": 100}]})
    res = r["results"]["A"]
    if res.get("error"): return "ERR " + res["error"][:80]
    return sum(1 for f in res.get("frames", []) if f.get("data", {}).get("values") and any(f["data"]["values"][1:] or [[]]))
def panels(ps):
    for p in ps:
        if p.get("type") == "row": yield from panels(p.get("panels", []))
        else: yield p
bad = 0
for d in api("/api/search?type=dash-db"):
    dash = api("/api/dashboards/uid/" + d["uid"])["dashboard"]
    variants = [{}]
    if d["uid"] == "node-exporter-full":   # check each host separately
        variants = [{"job": "node", "nodename": h, "node": inst} for h, inst in HOSTS.items()]
    for v in variants:
        ok = empty = 0; empties = []
        for p in panels(dash["panels"]):
            for t in p.get("targets", []):
                if not t.get("expr"): continue
                ds = (t.get("datasource") or p.get("datasource") or {}).get("uid") or "prometheus"
                if ds.startswith("$"): ds = "prometheus"   # templated (e.g. ${ds_prometheus} in #1860)
                n = run(subst(t["expr"], v), ds)
                if isinstance(n, int) and n > 0: ok += 1
                else: empty += 1; empties.append(f'{p.get("title","?")}: {n if isinstance(n,str) else "no data"}')
        tag = f' [{v.get("nodename")}]' if v else ""
        print(f'{d.get("folderTitle","-")} / {d["title"]}{tag}: {ok} queries with data, {empty} empty')
        for e in empties[:40]: print("     -", e)
        bad += empty if d["uid"] != "node-exporter-full" else 0
print("CUSTOM-DASHBOARD EMPTY QUERIES:", bad)
