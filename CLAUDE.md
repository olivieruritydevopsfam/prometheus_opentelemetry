# CLAUDE.md

## Objectif du lab

Lab local de collecte de métriques d'apps Java via OpenTelemetry, selon deux approches :
- `java-demo` : agent OTel (auto-instrumentation) → OTLP/HTTP ;
- `java-demo-jmx` : même app **sans agent**, lue en JMX/RMI par `jmx-scraper` → OTLP/HTTP.

Les deux poussent vers l'OTel Collector (OTLP → batch → exporter Prometheus) → Prometheus
(scrape + règles) → Alertmanager, et Grafana (datasources + 2 dashboards provisionnés).
Uniquement des métriques ; traces et logs sont désactivés côté agent.
Cible : macOS Apple Silicon + OrbStack, donc tout doit tourner en arm64.

## Commandes utiles

```bash
docker compose up -d --build              # démarrer / reconstruire l'app
./scripts/check.sh                        # valider app, JMX, Collector, Prometheus, Grafana, alerting
docker compose logs -f otel-collector     # l'exporter debug y logge les lots reçus
docker compose logs -f jmx-scraper        # connexion JMX et erreurs de règles YAML
curl -XPOST localhost:9090/-/reload       # recharger prometheus.yml et les règles sans redémarrer
docker compose restart otel-collector     # après modif de otel-collector/config.yaml
docker compose restart alertmanager       # après modif de alertmanager/alertmanager.yml
docker compose up -d --build jmx-scraper  # après modif de jmx-scraper/gc.yaml (copié dans l'image)
docker compose down -v                    # tout arrêter et vider les volumes
python3 grafana/tools/gen_dashboard.py grafana/dashboards   # régénérer les deux dashboards
curl -s localhost:8889/metrics | grep '^# TYPE'             # noms réels des métriques
curl -s localhost:9093/api/v2/alerts                        # alertes reçues par Alertmanager
```

Pas de Java ni de Maven sur la machine hôte : l'app se compile uniquement dans Docker
(Dockerfile multi-stage).

Valider une config sans démarrer la stack :

```bash
docker run --rm -v $PWD/otel-collector/config.yaml:/c.yaml otel/opentelemetry-collector-contrib:0.135.0 validate --config=/c.yaml
docker run --rm --entrypoint promtool -v $PWD/prometheus:/etc/prometheus prom/prometheus:v3.6.0 check config /etc/prometheus/prometheus.yml
docker run --rm --entrypoint promtool -v $PWD/prometheus:/p -w /p/tests prom/prometheus:v3.6.0 test rules java-demo.rules.test.yml
docker run --rm --entrypoint amtool -v $PWD/alertmanager/alertmanager.yml:/a.yml prom/alertmanager:v0.28.1 check-config /a.yml
```

## Conventions

- **Versions explicites** pour toutes les images et dépendances, jamais `latest`. Les versions de
  l'agent (`OTEL_AGENT_VERSION` dans le Dockerfile et `docker-compose.yml`) et de
  `opentelemetry-api` (`pom.xml`) se mettent à jour ensemble : l'API doit rester ≤ celle embarquée
  par l'agent. La version du jmx-scraper est dans `jmx-scraper/Dockerfile` et `docker-compose.yml`.
- **Aucun secret dans le dépôt** : identifiants via `.env` (ignoré par git), modèle dans
  `.env.example`.
- **PromQL** : vérifier les noms réels sur `:8889/metrics` ou dans Prometheus avant d'écrire une
  requête ; ne pas les deviner. Une montée de version de l'agent ou du Collector peut renommer
  métriques ou labels.
- **Dashboards** : `grafana/tools/gen_dashboard.py` est la source de vérité ; les JSON sont générés.
  Les panneaux HTTP de l'agent excluent `http_route=~"/actuator.*"` (trafic des healthchecks).
  Le dashboard agent filtre `job` sur l'histogramme HTTP, le dashboard JMX sur les métriques Tomcat.
- **Agent vs JMX** : ne pas supposer qu'une métrique de l'agent existe en JMX (pas d'histogramme
  HTTP ni GC, threads sans état). Les règles JMX maison (`jmx-scraper/gc.yaml`) utilisent des noms
  distincts de la semconv pour ne pas se mélanger avec ceux de l'agent.
- **Alertes** : toute règle nouvelle ou modifiée a un scénario dans `prometheus/tests/`. Les seuils
  doivent rester au-dessus du régime normal du load-generator (aucune alerte active au repos ;
  `check.sh` le vérifie).
- Métriques métier : via l'API OpenTelemetry (`GlobalOpenTelemetry.getMeter("com.example.demo")`),
  nom OTel en `point.separated`, avec unité. Pas de Micrometer.
- Un commit par étape logique, messages en français au format `type(portée): description`.
- Après tout changement, relancer `./scripts/check.sh`.
