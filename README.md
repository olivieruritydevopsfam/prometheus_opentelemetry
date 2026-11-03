# Lab OpenTelemetry → Prometheus → Grafana (Java)

Lab local pour simuler la collecte de métriques d'une application Java instrumentée
automatiquement par l'agent OpenTelemetry, puis les exploiter dans Grafana.
Testé sur macOS Apple Silicon (arm64) avec OrbStack.

## Architecture

```
 ┌──────────────┐  HTTP   ┌──────────────────────────┐
 │ load-generator│───────▶│ java-demo (Spring Boot)  │
 │   (k6)        │        │ + opentelemetry-javaagent│
 └──────────────┘         └────────────┬─────────────┘
                                       │ OTLP/HTTP :4318 (push toutes les 10 s)
                          ┌────────────▼─────────────┐
                          │ OpenTelemetry Collector  │
                          │ otlp → memory_limiter,   │
                          │ batch → prometheus,debug │
                          └────────────┬─────────────┘
                                       │ scrape :8889 (+ :8888 métriques internes)
                          ┌────────────▼─────────────┐
                          │ Prometheus               │
                          └────────────┬─────────────┘
                                       │ PromQL
                          ┌────────────▼─────────────┐
                          │ Grafana (datasource +    │
                          │ dashboard provisionnés)  │
                          └──────────────────────────┘
```

| Composant | Image / version |
|---|---|
| java-demo | Spring Boot 3.5.6, Java 21 (`eclipse-temurin:21.0.8_9-jre`), agent OTel Java 2.20.1 |
| Collector | `otel/opentelemetry-collector-contrib:0.135.0` |
| Prometheus | `prom/prometheus:v3.6.0` |
| Grafana | `grafana/grafana:12.2.0` |
| Générateur de charge | `grafana/k6:1.3.0` |

## Arborescence

```
apps/java-demo/            App Spring Boot + Dockerfile multi-stage (agent téléchargé au build)
otel-collector/config.yaml Pipeline metrics du Collector
prometheus/prometheus.yml  Cibles de scrape
grafana/provisioning/      Datasource et provider de dashboards
grafana/dashboards/        Dashboard JSON (généré)
grafana/tools/             Générateur du dashboard
load-generator/script.js   Scénario k6
scripts/check.sh           Vérification de bout en bout
```

## Démarrage / arrêt

```bash
docker compose up -d
```

Le premier démarrage compile l'app (~1–2 min). Les métriques apparaissent environ 30 s plus tard.

```bash
./scripts/check.sh
```

```bash
docker compose down
```

```bash
docker compose down -v
```

`down -v` supprime aussi les volumes Prometheus et Grafana.

Identifiants Grafana : `admin` / `admin` par défaut. Pour les changer, sans rien commiter :

```bash
cp .env.example .env
```

puis éditer `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD`. Un mot de passe déjà enregistré dans le
volume `grafana-data` n'est pas modifié : faire `docker compose down -v` pour repartir de zéro.

## URLs

| Service | URL |
|---|---|
| App | http://localhost:8080/hello, `/slow`, `/error`, `/order`, `/actuator/health` |
| Prometheus | http://localhost:9090 (cibles : http://localhost:9090/targets) |
| Grafana | http://localhost:3000 (dashboard *OTel Lab › Java demo — OpenTelemetry*, page d'accueil par défaut) |
| Collector – exporter Prometheus | http://localhost:8889/metrics |
| Collector – métriques internes | http://localhost:8888/metrics |
| Collector – health check | http://localhost:13133 |

## Endpoints de l'application

| Endpoint | Comportement |
|---|---|
| `GET /hello` | réponse immédiate |
| `GET /slow` | latence aléatoire de 200 ms à 2 s |
| `GET /error` | HTTP 500 environ une fois sur trois |
| `GET /order` | incrémente le compteur métier `orders.created` (attribut `product`) |

## Métriques exposées (relevées sur `:8889`)

Nommage OTel → Prometheus : les `.` deviennent `_`, l'unité est ajoutée en suffixe
(`_seconds`, `_bytes`, `_ratio`), et `_total` pour les compteurs.

| Métrique OTel | Série Prometheus | Labels utiles |
|---|---|---|
| `jvm.memory.used` | `jvm_memory_used_bytes` | `jvm_memory_type` (heap/non_heap), `jvm_memory_pool_name` |
| `jvm.memory.committed` / `.limit` | `jvm_memory_committed_bytes`, `jvm_memory_limit_bytes` | idem (seul G1 Old Gen a une limite, ≈ `-Xmx`) |
| `jvm.gc.duration` | `jvm_gc_duration_seconds_{bucket,sum,count}` | `jvm_gc_name`, `jvm_gc_action` |
| `jvm.thread.count` | `jvm_thread_count` | `jvm_thread_state`, `jvm_thread_daemon` |
| `jvm.cpu.recent_utilization` | `jvm_cpu_recent_utilization_ratio` | — |
| `jvm.cpu.time` | `jvm_cpu_time_seconds_total` | — |
| `http.server.request.duration` | `http_server_request_duration_seconds_{bucket,sum,count}` | `http_route`, `http_request_method`, `http_response_status_code`, `error_type` |
| `orders.created` (custom) | `orders_created_total` | `product` |

`job` = `service.name` (`java-demo`) et `instance` = `service.instance.id` (UUID, change à
chaque redémarrage), conservés grâce à `honor_labels: true`. Les autres attributs de
ressource sont dans `target_info`.

## Requêtes PromQL utiles

```promql
# Débit par route (hors healthcheck)
sum by (http_route) (rate(http_server_request_duration_seconds_count{job="java-demo", http_route!~"/actuator.*"}[1m]))

# Latence p95 par route
histogram_quantile(0.95, sum by (le, http_route) (rate(http_server_request_duration_seconds_bucket{job="java-demo"}[5m])))

# Taux d'erreurs 5xx global
sum(rate(http_server_request_duration_seconds_count{job="java-demo", http_response_status_code=~"5.."}[5m]))
  / sum(rate(http_server_request_duration_seconds_count{job="java-demo"}[5m]))

# Heap utilisée / max
sum(jvm_memory_used_bytes{jvm_memory_type="heap"}) / sum(jvm_memory_limit_bytes{jvm_memory_type="heap"})

# Durée moyenne d'une pause GC par collecteur
sum by (jvm_gc_name) (rate(jvm_gc_duration_seconds_sum[5m])) / sum by (jvm_gc_name) (rate(jvm_gc_duration_seconds_count[5m]))

# Threads par état
sum by (jvm_thread_state) (jvm_thread_count)

# CPU de la JVM (part de la machine)
jvm_cpu_recent_utilization_ratio

# Commandes par minute et par produit
sum by (product) (rate(orders_created_total[5m])) * 60

# Santé du pipeline : points reçus et exportés par le Collector
rate(otelcol_receiver_accepted_metric_points_total[1m])
rate(otelcol_exporter_sent_metric_points_total[1m])

# Joindre un attribut de ressource (ex. service_version) à une métrique
jvm_thread_count * on (job, instance) group_left (service_version) target_info
```

## Points d'attention

- Le Collector `contrib` est distroless (ni shell ni curl) : pas de healthcheck Docker possible.
  Sa santé se lit sur `:13133` et via `up{job=~"otel-collector.*"}` dans Prometheus.
- Les buckets par défaut de `jvm.gc.duration` (0.01 / 0.1 / 1 / 10 s) sont trop larges pour des
  quantiles exploitables : le dashboard utilise `sum / count`.
- Les quantiles HTTP sont interpolés dans les buckets (…, 1, 2.5, 5 s) : le p99 de `/slow` peut
  dépasser 2 s alors que la latence réelle ne dépasse pas 2 s.
- Traces et logs sont désactivés (`OTEL_TRACES_EXPORTER=none`, `OTEL_LOGS_EXPORTER=none`)
  tant que le lab n'a pas de backend pour eux.

## Modifier le dashboard

Le JSON est généré, ne pas l'éditer à la main :

```bash
python3 grafana/tools/gen_dashboard.py grafana/dashboards/java-demo.json
```

Grafana recharge le fichier en ~30 s.
