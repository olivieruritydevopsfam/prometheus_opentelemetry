"""Génère les dashboards de grafana/dashboards/ (source de vérité) :
java-demo.json (agent OTel) et java-jmx.json (JMX, sans agent).

Usage : python3 grafana/tools/gen_dashboard.py grafana/dashboards
Grafana recharge le fichier provisionné en ~30 s, sans redémarrage.
Les noms de métriques/labels ont été relevés sur l'endpoint :8889 du Collector
(agent OTel Java 2.20.1, jmx-scraper 1.61.0-alpha, Collector contrib 0.135.0).
"""
import json, sys

DS = {"type": "prometheus", "uid": "prometheus"}
SEL = 'job=~"$job", instance=~"$instance"'
HTTP = SEL + ', http_route!~"/actuator.*"'
pid = 0


def target(expr, legend, ref):
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "range": True}


def ts(title, targets, x, y, w=12, h=8, unit="short", stack=False, desc="", minv=None, maxv=None):
    global pid
    pid += 1
    defaults = {
        "unit": unit,
        "custom": {
            "drawStyle": "line", "lineWidth": 1, "fillOpacity": 25 if stack else 10,
            "showPoints": "never", "spanNulls": True,
            "stacking": {"mode": "normal" if stack else "none", "group": "A"},
        },
    }
    if minv is not None:
        defaults["min"] = minv
    if maxv is not None:
        defaults["max"] = maxv
    return {
        "id": pid, "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": [target(e, l, chr(65 + i)) for i, (e, l) in enumerate(targets)],
    }


def stat(title, expr, x, y, w=6, h=4, unit="short", thresholds=None, desc="", decimals=2):
    global pid
    pid += 1
    steps = thresholds or [{"color": "green", "value": None}]
    return {
        "id": pid, "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals,
                                     "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background", "graphMode": "area", "textMode": "auto"},
        "targets": [target(expr, "", "A")],
    }


def row(title, y):
    global pid
    pid += 1
    return {"id": pid, "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def q(phi):
    return (f'histogram_quantile({phi}, sum by (le) '
            f'(rate(http_server_request_duration_seconds_bucket{{{HTTP}}}[$__rate_interval])))')


RATE_ALL = f'sum(rate(http_server_request_duration_seconds_count{{{HTTP}}}[$__rate_interval]))'
RATE_5XX = (f'sum(rate(http_server_request_duration_seconds_count'
            f'{{{HTTP}, http_response_status_code=~"5.."}}[$__rate_interval]))')
ERR_RATIO = f'({RATE_5XX} or vector(0)) / {RATE_ALL}'

panels = [
    # --- Vue d'ensemble
    stat("Débit HTTP", RATE_ALL, 0, 0, unit="reqps"),
    stat("Taux d'erreurs 5xx", ERR_RATIO, 6, 0, unit="percentunit",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.05},
                     {"color": "red", "value": 0.15}]),
    stat("Latence p95", q(0.95), 12, 0, unit="s",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.5},
                     {"color": "red", "value": 1.5}]),
    stat("Heap utilisée / max",
         f'sum(jvm_memory_used_bytes{{{SEL}, jvm_memory_type="heap"}}) / '
         f'sum(jvm_memory_limit_bytes{{{SEL}, jvm_memory_type="heap"}})',
         18, 0, unit="percentunit",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.7},
                     {"color": "red", "value": 0.9}],
         desc="Seul le pool G1 Old Gen expose une limite : elle correspond à -Xmx."),

    # --- JVM
    row("JVM", 4),
    ts("Mémoire heap", [
        (f'sum(jvm_memory_used_bytes{{{SEL}, jvm_memory_type="heap"}})', "utilisée"),
        (f'sum(jvm_memory_committed_bytes{{{SEL}, jvm_memory_type="heap"}})', "committed"),
        (f'sum(jvm_memory_limit_bytes{{{SEL}, jvm_memory_type="heap"}})', "max"),
    ], 0, 5, unit="bytes"),
    ts("Heap par pool", [
        (f'sum by (jvm_memory_pool_name) (jvm_memory_used_bytes{{{SEL}, jvm_memory_type="heap"}})',
         "{{jvm_memory_pool_name}}"),
    ], 12, 5, unit="bytes", stack=True),
    ts("GC — collections / s", [
        (f'sum by (jvm_gc_name, jvm_gc_action) (rate(jvm_gc_duration_seconds_count{{{SEL}}}[$__rate_interval]))',
         "{{jvm_gc_name}} ({{jvm_gc_action}})"),
    ], 0, 13, unit="ops"),
    ts("GC — durée moyenne et temps passé", [
        (f'sum by (jvm_gc_name) (rate(jvm_gc_duration_seconds_sum{{{SEL}}}[$__rate_interval])) / '
         f'sum by (jvm_gc_name) (rate(jvm_gc_duration_seconds_count{{{SEL}}}[$__rate_interval]))',
         "durée moy. {{jvm_gc_name}}"),
        (f'sum(rate(jvm_gc_duration_seconds_sum{{{SEL}}}[$__rate_interval]))', "temps GC / s (toutes)"),
    ], 12, 13, unit="s",
       desc="Les buckets de jvm.gc.duration sont trop larges pour des quantiles utiles : on utilise sum/count."),
    ts("Threads par état", [
        (f'sum by (jvm_thread_state) (jvm_thread_count{{{SEL}}})', "{{jvm_thread_state}}"),
    ], 0, 21, stack=True),
    ts("CPU", [
        (f'avg(jvm_cpu_recent_utilization_ratio{{{SEL}}})', "JVM (part de la machine)"),
        (f'sum(rate(jvm_cpu_time_seconds_total{{{SEL}}}[$__rate_interval])) / '
         f'sum(jvm_cpu_count{{{SEL}}})', "JVM (cpu_time / nb CPU)"),
    ], 12, 21, unit="percentunit", minv=0),

    # --- HTTP
    row("HTTP", 29),
    ts("Débit par route", [
        (f'sum by (http_route) (rate(http_server_request_duration_seconds_count{{{HTTP}}}[$__rate_interval]))',
         "{{http_route}}"),
    ], 0, 30, unit="reqps", stack=True),
    ts("Débit par code de statut", [
        (f'sum by (http_response_status_code) '
         f'(rate(http_server_request_duration_seconds_count{{{HTTP}}}[$__rate_interval]))',
         "{{http_response_status_code}}"),
    ], 12, 30, unit="reqps", stack=True),
    ts("Latence p50 / p95 / p99 (toutes routes)", [
        (q(0.5), "p50"), (q(0.95), "p95"), (q(0.99), "p99"),
    ], 0, 38, unit="s"),
    ts("Latence p95 par route", [
        (f'histogram_quantile(0.95, sum by (le, http_route) '
         f'(rate(http_server_request_duration_seconds_bucket{{{HTTP}}}[$__rate_interval])))', "{{http_route}}"),
    ], 12, 38, unit="s"),
    ts("Taux d'erreurs 5xx", [
        (ERR_RATIO, "global"),
        (f'sum by (http_route) (rate(http_server_request_duration_seconds_count'
         f'{{{HTTP}, http_response_status_code=~"5.."}}[$__rate_interval])) / '
         f'sum by (http_route) (rate(http_server_request_duration_seconds_count{{{HTTP}}}[$__rate_interval]))',
         "{{http_route}}"),
    ], 0, 46, w=24, unit="percentunit", minv=0),

    # --- Métier
    row("Métier", 54),
    ts("Commandes créées / min par produit", [
        (f'sum by (product) (rate(orders_created_total{{{SEL}}}[$__rate_interval])) * 60', "{{product}}"),
    ], 0, 55, w=16, unit="short", stack=True),
    stat("Commandes sur la période", f'round(sum(increase(orders_created_total{{{SEL}}}[$__range])))',
         16, 55, w=8, h=8, unit="short", decimals=0,
         desc="increase() extrapole aux bornes de la fenêtre : valeur arrondie, approximative."),
]


def var(name, label, query):
    return {
        "name": name, "label": label, "type": "query", "datasource": DS,
        "query": {"query": query, "refId": "PrometheusVariableQueryEditor-VariableQuery", "qryType": 1},
        "definition": query, "refresh": 2, "includeAll": True, "multi": True,
        "allValue": ".*", "current": {"text": "All", "value": "$__all"}, "sort": 1,
    }


def dashboard(uid, title, description, tags, variables, panels):
    return {
        "uid": uid, "title": title, "description": description, "tags": tags,
        "timezone": "browser", "editable": True, "graphTooltip": 1, "refresh": "10s",
        "schemaVersion": 41, "time": {"from": "now-30m", "to": "now"},
        "templating": {"list": variables}, "annotations": {"list": []}, "panels": panels,
    }


# L'histogramme HTTP n'existe qu'avec l'agent : la variable exclut ainsi les JVM collectées via JMX
agent_dashboard = dashboard(
    "java-demo-otel", "Java demo — OpenTelemetry",
    "Métriques JVM, HTTP et métier de java-demo (agent OTel -> Collector -> Prometheus)",
    ["otel", "java", "jvm", "agent"],
    [var("job", "Service", "label_values(http_server_request_duration_seconds_count, job)"),
     var("instance", "Instance", 'label_values(http_server_request_duration_seconds_count{job=~"$job"}, instance)')],
    panels,
)

# --- Dashboard JMX (sans agent) -------------------------------------------------
# Métriques lues par jmx-scraper (cibles jvm,tomcat + jmx-scraper/gc.yaml). Différences avec l'agent :
# pas d'histogramme HTTP (latence moyenne/max seulement), threads sans état, GC en cumuls.
pid = 0
TC = SEL  # mêmes variables job/instance

TC_RATE = f'sum(rate(tomcat_request_count_total{{{TC}}}[$__rate_interval]))'
TC_ERR = f'sum(rate(tomcat_error_count_total{{{TC}}}[$__rate_interval]))'
TC_AVG = f'sum(rate(tomcat_request_duration_sum_seconds_total{{{TC}}}[$__rate_interval])) / {TC_RATE}'
HEAP_RATIO = (f'sum(jvm_memory_used_bytes{{{SEL}, jvm_memory_type="heap"}}) / '
              f'sum(jvm_memory_limit_bytes{{{SEL}, jvm_memory_type="heap"}})')
BOTH = 'job=~"java-demo|java-demo-jmx"'

jmx_panels = [
    stat("Débit Tomcat", TC_RATE, 0, 0, unit="reqps"),
    stat("Erreurs Tomcat (≥ 400)", f'{TC_ERR} / {TC_RATE}', 6, 0, unit="percentunit",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.05},
                     {"color": "red", "value": 0.15}],
         desc="errorCount du GlobalRequestProcessor : compte les réponses de statut ≥ 400."),
    stat("Latence moyenne", TC_AVG, 12, 0, unit="s",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.5},
                     {"color": "red", "value": 1.5}],
         desc="JMX ne fournit qu'un cumul de durée : pas de percentiles possibles."),
    stat("Heap utilisée / max", HEAP_RATIO, 18, 0, unit="percentunit",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.7},
                     {"color": "red", "value": 0.9}]),

    row("JVM (MBeans java.lang)", 4),
    ts("Mémoire heap", [
        (f'sum(jvm_memory_used_bytes{{{SEL}, jvm_memory_type="heap"}})', "utilisée"),
        (f'sum(jvm_memory_committed_bytes{{{SEL}, jvm_memory_type="heap"}})', "committed"),
        (f'sum(jvm_memory_limit_bytes{{{SEL}, jvm_memory_type="heap"}})', "max"),
    ], 0, 5, unit="bytes"),
    ts("Mémoire par pool", [
        (f'sum by (jvm_memory_pool_name) (jvm_memory_used_bytes{{{SEL}}})', "{{jvm_memory_pool_name}}"),
    ], 12, 5, unit="bytes", stack=True),
    ts("GC — collections / s", [
        (f'sum by (jvm_gc_name) (rate(jvm_gc_collection_count_total{{{SEL}}}[$__rate_interval]))',
         "{{jvm_gc_name}}"),
    ], 0, 13, unit="ops", desc="Règle maison jmx-scraper/gc.yaml (MBean GarbageCollector.CollectionCount)."),
    ts("GC — durée moyenne et temps passé", [
        (f'sum by (jvm_gc_name) (rate(jvm_gc_collection_time_seconds_total{{{SEL}}}[$__rate_interval])) / '
         f'sum by (jvm_gc_name) (rate(jvm_gc_collection_count_total{{{SEL}}}[$__rate_interval]))',
         "durée moy. {{jvm_gc_name}}"),
        (f'sum(rate(jvm_gc_collection_time_seconds_total{{{SEL}}}[$__rate_interval]))', "temps GC / s (toutes)"),
    ], 12, 13, unit="s"),
    ts("Threads", [
        (f'sum(jvm_thread_count{{{SEL}}})', "JVM (total)"),
        (f'sum(tomcat_thread_count{{{SEL}}})', "pool Tomcat"),
        (f'sum(tomcat_thread_busy_count{{{SEL}}})', "Tomcat occupés"),
    ], 0, 21, desc="Via JMX, jvm.thread.count n'a pas de détail par état."),
    ts("CPU", [
        (f'avg(jvm_cpu_recent_utilization_ratio{{{SEL}}})', "JVM (part de la machine)"),
        (f'avg(jvm_system_cpu_utilization_ratio{{{SEL}}})', "système"),
    ], 12, 21, unit="percentunit", minv=0),

    row("Tomcat (MBeans Catalina)", 29),
    ts("Requêtes et erreurs / s", [
        (TC_RATE, "requêtes"), (TC_ERR, "erreurs (≥ 400)"),
    ], 0, 30, unit="reqps"),
    ts("Latence moyenne et max", [
        (TC_AVG, "moyenne"),
        (f'max(tomcat_request_duration_max_seconds{{{TC}}})', "max depuis le démarrage"),
    ], 12, 30, unit="s",
       desc="maxTime est le maximum depuis le démarrage de Tomcat, pas sur la fenêtre affichée."),
    ts("Pool de threads Tomcat", [
        (f'sum(tomcat_thread_busy_count{{{TC}}})', "occupés"),
        (f'sum(tomcat_thread_count{{{TC}}})', "créés"),
        (f'sum(tomcat_thread_limit{{{TC}}})', "max"),
    ], 0, 38),
    ts("Trafic réseau", [
        (f'sum by (network_io_direction) (rate(tomcat_network_io_bytes_total{{{TC}}}[$__rate_interval]))',
         "{{network_io_direction}}"),
    ], 12, 38, unit="Bps"),

    row("Comparaison agent / JMX (même app)", 46),
    ts("Heap utilisée", [
        (f'sum by (job) (jvm_memory_used_bytes{{{BOTH}, jvm_memory_type="heap"}})', "{{job}}"),
    ], 0, 47, w=8, unit="bytes"),
    ts("CPU JVM", [
        (f'avg by (job) (jvm_cpu_recent_utilization_ratio{{{BOTH}}})', "{{job}}"),
    ], 8, 47, w=8, unit="percentunit", minv=0),
    ts("Latence moyenne HTTP", [
        ('sum(rate(http_server_request_duration_seconds_sum{job="java-demo", http_route!~"/actuator.*"}[$__rate_interval])) / '
         'sum(rate(http_server_request_duration_seconds_count{job="java-demo", http_route!~"/actuator.*"}[$__rate_interval]))',
         "java-demo (agent, histogramme)"),
        ('sum(rate(tomcat_request_duration_sum_seconds_total{job="java-demo-jmx"}[$__rate_interval])) / '
         'sum(rate(tomcat_request_count_total{job="java-demo-jmx"}[$__rate_interval]))',
         "java-demo-jmx (JMX, cumul Tomcat)"),
    ], 16, 47, w=8, unit="s", desc="Le côté JMX inclut les healthchecks /actuator/health (non filtrables)."),
]

jmx_dashboard = dashboard(
    "java-jmx", "JVM via JMX — sans agent",
    "Métriques JVM et Tomcat lues par le jmx-scraper OpenTelemetry sur une JVM sans agent",
    ["otel", "java", "jvm", "jmx"],
    [var("job", "Service", "label_values(tomcat_request_count_total, job)"),
     var("instance", "Instance", 'label_values(tomcat_request_count_total{job=~"$job"}, instance)')],
    jmx_panels,
)

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "grafana/dashboards"
    for name, d in (("java-demo.json", agent_dashboard), ("java-jmx.json", jmx_dashboard)):
        with open(f"{out}/{name}", "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(name, len(d["panels"]), "panels")
