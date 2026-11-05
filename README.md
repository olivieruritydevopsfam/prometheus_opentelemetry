# Lab OpenTelemetry → Prometheus → Grafana (Java)

Lab local pour simuler la collecte de métriques d'une application Java instrumentée
automatiquement par l'agent OpenTelemetry, puis les exploiter dans Grafana.
Testé sur macOS Apple Silicon (arm64) avec OrbStack.

## Architecture

```
                ┌─────────────────────────┐        ┌──────────────────────────┐
  load-        │ java-demo               │        │ java-demo-jmx            │
  generator ──▶│ + opentelemetry-javaagent│        │ SANS agent, JMX :9010    │◀── load-generator
  (k6)         └────────────┬────────────┘        └────────────┬─────────────┘
                            │ OTLP/HTTP (push 10 s)            │ JMX/RMI (lecture des MBeans)
                            │                     ┌────────────▼─────────────┐
                            │                     │ jmx-scraper (OTel)       │
                            │                     └────────────┬─────────────┘
                            │                                  │ OTLP/HTTP (push 10 s)
                          ┌─▼──────────────────────────────────▼─┐
                          │ OpenTelemetry Collector              │
                          │ otlp → memory_limiter, batch →       │
                          │ prometheus (:8889), debug            │
                          └──────────────────┬───────────────────┘
                                             │ scrape :8889 (+ :8888 métriques internes)
                          ┌──────────────────▼───────────────────┐    alertes   ┌──────────────┐
                          │ Prometheus (+ règles d'alerte)       │─────────────▶│ Alertmanager │
                          └──────────────────┬───────────────────┘              └──────┬───────┘
                                             │ PromQL                                  │
                          ┌──────────────────▼─────────────────────────────────────────▼┐
                          │ Grafana : datasources Prometheus + Alertmanager, 2 dashboards │
                          └───────────────────────────────────────────────────────────────┘
```

| Composant | Image / version |
|---|---|
| java-demo, java-demo-jmx | Spring Boot 3.5.6, Java 21 (`eclipse-temurin:21.0.8_9-jre`), agent OTel Java 2.20.1 (java-demo seulement) |
| jmx-scraper | `opentelemetry-jmx-scraper` 1.61.0-alpha sur `eclipse-temurin:21.0.8_9-jre` |
| Collector | `otel/opentelemetry-collector-contrib:0.135.0` |
| Prometheus | `prom/prometheus:v3.6.0` |
| Alertmanager | `prom/alertmanager:v0.28.1` |
| Grafana | `grafana/grafana:12.2.0` |
| Générateur de charge | `grafana/k6:1.3.0` |

## Arborescence

```
apps/java-demo/            App Spring Boot + Dockerfile multi-stage (agent téléchargé au build)
jmx-scraper/               Image du jmx-scraper + règle JMX maison pour le GC (gc.yaml)
otel-collector/config.yaml Pipeline metrics du Collector
prometheus/prometheus.yml  Cibles de scrape, règles, Alertmanager
prometheus/rules/          Règles d'enregistrement et d'alerte
prometheus/tests/          Tests unitaires des règles (promtool)
alertmanager/              Routage, inhibitions
grafana/provisioning/      Datasources et provider de dashboards
grafana/dashboards/        Dashboards JSON (générés)
grafana/tools/             Générateur des dashboards
load-generator/script.js   Scénario k6
scripts/check.sh           Vérification de bout en bout
scripts/deploy-rules.sh    Validation, déploiement et rechargement des règles (local ou CI)
bamboo-specs/bamboo.yml    Exemple de plan Bamboo pour les règles
docs/                      Guides (règles Prometheus en CI, exercice heap) et patchs d'exercice
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
| App (agent) | http://localhost:8080/hello, `/slow`, `/error`, `/order`, `/actuator/health` |
| App (sans agent, JMX) | http://localhost:8081/hello, mêmes endpoints |
| Prometheus | http://localhost:9090 (cibles : `/targets`, alertes : `/alerts`) |
| Alertmanager | http://localhost:9093 |
| Grafana | http://localhost:3000 : dossier *OTel Lab*, dashboards *Java demo — OpenTelemetry* (accueil) et *JVM via JMX — sans agent* |
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
| `POST /leak?mb=N` | garde N Mo en heap (fuite simulée, 1 à 500) ; jamais appelé par le load-generator |
| `DELETE /leak` | relâche la fuite et lance un GC |

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

## Collecte JVM sans agent (JMX)

Pour les JVM qu'on ne peut pas redémarrer avec l'agent (produit tiers, middleware, app legacy),
le [jmx-scraper](https://github.com/open-telemetry/opentelemetry-java-contrib/tree/main/jmx-scraper)
OpenTelemetry tourne **à côté** de la JVM. Il se connecte en JMX/RMI, lit les MBeans à chaque
intervalle d'export et pousse le résultat en OTLP vers le même Collector. Le seul changement côté
JVM cible, ce sont les flags JMX distant :

```
-Dcom.sun.management.jmxremote.port=9010 -Dcom.sun.management.jmxremote.rmi.port=9010
-Dcom.sun.management.jmxremote.authenticate=false -Dcom.sun.management.jmxremote.ssl=false
-Djava.rmi.server.hostname=java-demo-jmx
```

> ⚠️ Sans authentification ni TLS : acceptable uniquement sur le réseau Docker du lab (le port 9010
> n'est pas publié). En vrai, activer `jmxremote.authenticate`/`ssl` et passer
> `OTEL_JMX_USERNAME`/`OTEL_JMX_PASSWORD` au scraper depuis un secret.

Configuration du scraper (`docker-compose.yml`) : `OTEL_JMX_TARGET_SYSTEM=jvm,tomcat`,
`OTEL_JMX_TARGET_SOURCE=instrumentation` (définitions alignées sur les conventions sémantiques), et
`OTEL_JMX_CONFIG=/etc/jmx-scraper/gc.yaml` pour une règle maison. D'autres cibles prêtes à l'emploi
existent (kafka, jetty, wildfly, activemq, cassandra…), et `gc.yaml` montre comment mapper n'importe
quel MBean.

### Agent vs JMX : ce qu'on obtient (relevé sur `:8889`)

| Besoin | Agent (`job="java-demo"`) | JMX (`job="java-demo-jmx"`) |
|---|---|---|
| Heap / non-heap par pool | `jvm_memory_*_bytes` | identique |
| CPU | `jvm_cpu_recent_utilization_ratio`, `jvm_cpu_time_seconds_total` | identique, plus `jvm_system_cpu_utilization_ratio` et `jvm_system_cpu_load_1m` |
| Threads | `jvm_thread_count` par `jvm_thread_state` | `jvm_thread_count` total seulement |
| GC | histogramme `jvm_gc_duration_seconds_*` par pause | cumuls `jvm_gc_collection_count_total`, `jvm_gc_collection_time_seconds_total` (règle `gc.yaml`) |
| HTTP | histogramme `http_server_request_duration_seconds_*` par route et code → **p50/p95/p99** | compteurs Tomcat `tomcat_request_count_total`, `tomcat_error_count_total` (statut ≥ 400), `tomcat_request_duration_sum_seconds_total` → **latence moyenne seulement**, `tomcat_request_duration_max_seconds` (max depuis le démarrage) |
| Pool de threads HTTP | — | `tomcat_thread_count`, `tomcat_thread_busy_count`, `tomcat_thread_limit` |
| Métriques métier (API OTel) | `orders_created_total` | aucune (l'API OTel est no-op sans agent ni SDK) |
| Descripteurs de fichiers, buffers | — | `jvm_file_descriptor_*`, `jvm_buffer_*` |

Pour une Spring Boot avec Tomcat embarqué, les MBeans Tomcat ne sont publiés qu'avec
`server.tomcat.mbeanregistry.enabled=true` (variable `SERVER_TOMCAT_MBEANREGISTRY_ENABLED`).

```promql
# Latence moyenne et taux d'erreurs via JMX
sum(rate(tomcat_request_duration_sum_seconds_total{job="java-demo-jmx"}[5m])) / sum(rate(tomcat_request_count_total{job="java-demo-jmx"}[5m]))
sum(rate(tomcat_error_count_total{job="java-demo-jmx"}[5m])) / sum(rate(tomcat_request_count_total{job="java-demo-jmx"}[5m]))

# Saturation du pool de threads Tomcat
sum(tomcat_thread_busy_count{job="java-demo-jmx"}) / sum(tomcat_thread_limit{job="java-demo-jmx"})

# Temps passé en GC (part du temps)
sum(rate(jvm_gc_collection_time_seconds_total{job="java-demo-jmx"}[5m]))
```

## Alerting

Prometheus évalue les règles de [prometheus/rules/java-demo.rules.yml](prometheus/rules/java-demo.rules.yml)
toutes les 15 s et envoie les alertes à Alertmanager (http://localhost:9093). Elles sont aussi
visibles dans Grafana (*Alerting › Alert rules*, datasource *Alertmanager*). Aucune notification
ne sort du lab : le receiver par défaut `lab-null` ne fait rien (voir
[alertmanager/alertmanager.yml](alertmanager/alertmanager.yml) pour brancher Slack, e-mail ou webhook).

| Alerte | Condition | Sévérité |
|---|---|---|
| `JavaDemoMetricsAbsent` | plus de métrique JVM de `java-demo` (≈ 3 min après l'arrêt) | critical |
| `JavaJmxMetricsAbsent` | plus de métrique JMX de `java-demo-jmx` | critical |
| `OtelCollectorDown` | cible Collector `up == 0` depuis 1 min | critical |
| `OtelCollectorDroppingMetrics` | points refusés ou en échec d'export depuis 5 min | warning |
| `JavaDemoHighErrorRate` | 5xx > 10 % (warning) / > 25 % (critical) depuis 2 min | warning / critical |
| `JavaDemoHighLatencyP95` | p95 d'une route > 2,5 s depuis 5 min | warning |
| `JavaDemoHeapUsageHigh` | heap > 90 % du max depuis 5 min (agent et JMX) | warning |
| `JavaDemoGCTimeHigh` | > 10 % du temps en GC depuis 5 min | warning |
| `JavaDemoNoOrders` | `orders_created_total` n'augmente plus depuis 5 min | warning |

Les seuils sont au-dessus du régime normal du load-generator (≈ 5 % de 5xx, p95 de `/slow`
≈ 2,4 s) : en temps normal, **aucune alerte n'est active**. Inhibitions : un critical masque le
warning de la même alerte ; `JavaDemoMetricsAbsent` masque `JavaDemoNoOrders`.

Déclencher une alerte pour de vrai :

```bash
docker compose stop java-demo
```

Environ 3 min plus tard, `JavaDemoMetricsAbsent` est *firing* dans Alertmanager. Pour revenir :

```bash
docker compose start java-demo
```

Tests unitaires des règles (10 scénarios) :

```bash
docker run --rm --entrypoint promtool -v "$PWD/prometheus:/p" -w /p/tests prom/prometheus:v3.6.0 test rules java-demo.rules.test.yml
```

Modifier une règle, la valider puis la recharger sans redémarrer Prometheus :

```bash
./scripts/deploy-rules.sh validate   # promtool check + test
./scripts/deploy-rules.sh deploy     # validate, POST /-/reload, vérification des règles chargées
```

Le guide [docs/regles-prometheus-ci.md](docs/regles-prometheus-ci.md) explique le chargement
des règles et le déploiement depuis une CI (exemple Bamboo). Il contient aussi un exercice
complet : changer un seuil et voir l'effet dans la rangée *Règles et alertes Prometheus* du
dashboard et dans Grafana Alerting.

Un second exercice, [docs/exercice-heap.md](docs/exercice-heap.md), simule une fuite mémoire :
découper l'alerte heap en deux paliers (warning 70 %, critical 90 %), la mettre en silence
avec `amtool`, puis vérifier le retour à la normale. La heap de `java-demo` est fixée à 512 Mo
(`JDK_JAVA_OPTIONS: -Xmx512m`) pour que la fuite remplisse la heap en quelques secondes.

## Points d'attention

- Le Collector `contrib` est distroless (ni shell ni curl) : pas de healthcheck Docker possible.
  Sa santé se lit sur `:13133` et via `up{job=~"otel-collector.*"}` dans Prometheus.
- Les buckets par défaut de `jvm.gc.duration` (0.01 / 0.1 / 1 / 10 s) sont trop larges pour des
  quantiles exploitables : le dashboard utilise `sum / count`.
- Les quantiles HTTP sont interpolés dans les buckets (…, 1, 2.5, 5 s) : le p99 de `/slow` peut
  dépasser 2 s alors que la latence réelle ne dépasse pas 2 s.
- Traces et logs sont désactivés (`OTEL_TRACES_EXPORTER=none`, `OTEL_LOGS_EXPORTER=none`)
  tant que le lab n'a pas de backend pour eux.

- Le Collector oublie une série 1 min après le dernier export (`metric_expiration: 1m`) : c'est
  ce qui permet aux alertes d'absence de se déclencher vite.

## Modifier les dashboards

Les JSON sont générés, ne pas les éditer à la main :

```bash
python3 grafana/tools/gen_dashboard.py grafana/dashboards
```

Grafana recharge le fichier en ~30 s.
