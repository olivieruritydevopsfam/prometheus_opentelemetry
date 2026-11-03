#!/usr/bin/env bash
# Vérifie de bout en bout la stack : app -> Collector -> Prometheus -> Grafana.
# Usage : ./scripts/check.sh   (stack démarrée, ~1 min après `docker compose up -d`)
set -uo pipefail

APP=${APP:-http://localhost:8080}
PROM=${PROM:-http://localhost:9090}
GRAFANA=${GRAFANA:-http://localhost:3000}
GF_AUTH="${GF_ADMIN_USER:-admin}:${GF_ADMIN_PASSWORD:-admin}"

fail=0
ok()  { printf '  \033[32mOK\033[0m  %s\n' "$1"; }
ko()  { printf '  \033[31mKO\033[0m  %s\n' "$1"; fail=1; }

# Nombre de séries renvoyées par une requête PromQL
prom_count() {
  curl -s "$PROM/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["data"]["result"]))' 2>/dev/null || echo 0
}

echo "a) Application"
for path in hello slow error order actuator/health; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$APP/$path")
  # /error renvoie 500 environ une fois sur trois
  if [[ $code == 200 || ($path == error && $code == 500) ]]; then ok "/$path -> $code"; else ko "/$path -> $code"; fi
done

echo "b) Collector"
n=$(prom_count 'sum(rate(otelcol_receiver_accepted_metric_points_total{receiver="otlp"}[1m])) > 0')
[[ $n -gt 0 ]] && ok "le receiver OTLP accepte des points" || ko "aucun point reçu par le receiver OTLP"
n=$(prom_count 'up{job=~"otel-collector.*"} == 1')
[[ $n -eq 2 ]] && ok "cibles Collector (8888, 8889) up" || ko "cibles Collector up : $n/2"

echo "c) Prometheus"
for m in jvm_memory_used_bytes jvm_gc_duration_seconds_count jvm_thread_count jvm_cpu_recent_utilization_ratio \
         http_server_request_duration_seconds_bucket orders_created_total; do
  n=$(prom_count "$m{job=\"java-demo\"}")
  [[ $n -gt 0 ]] && ok "$m ($n séries)" || ko "$m absente"
done

echo "d) Grafana"
curl -sf -u "$GF_AUTH" "$GRAFANA/api/datasources/uid/prometheus/health" | grep -q '"status":"OK"' \
  && ok "datasource Prometheus joignable" || ko "datasource Prometheus en erreur"
curl -sf -u "$GF_AUTH" "$GRAFANA/api/dashboards/uid/java-demo-otel" | grep -q '"provisioned":true' \
  && ok "dashboard java-demo-otel provisionné" || ko "dashboard java-demo-otel introuvable"

exit $fail
