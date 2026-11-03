# CLAUDE.md

## Objectif du lab

Lab local de collecte de métriques d'une app Java via OpenTelemetry :
`java-demo` (agent OTel, auto-instrumentation, OTLP/HTTP) → OTel Collector (OTLP → batch →
exporter Prometheus) → Prometheus (scrape) → Grafana (datasource + dashboard provisionnés).
Uniquement des métriques pour l'instant ; traces et logs sont désactivés côté agent.
Cible : macOS Apple Silicon + OrbStack, donc tout doit tourner en arm64.

## Commandes utiles

```bash
docker compose up -d --build              # démarrer / reconstruire l'app
./scripts/check.sh                        # valider app, Collector, Prometheus, Grafana
docker compose logs -f otel-collector     # l'exporter debug y logge les lots reçus
docker compose restart prometheus         # après modif de prometheus/prometheus.yml
docker compose restart otel-collector     # après modif de otel-collector/config.yaml
docker compose down -v                    # tout arrêter et vider les volumes
python3 grafana/tools/gen_dashboard.py grafana/dashboards/java-demo.json   # régénérer le dashboard
curl -s localhost:8889/metrics | grep '^# TYPE'                             # noms réels des métriques
```

Pas de Java ni de Maven sur la machine hôte : l'app se compile uniquement dans Docker
(Dockerfile multi-stage).

Valider une config sans démarrer la stack :

```bash
docker run --rm -v $PWD/otel-collector/config.yaml:/c.yaml otel/opentelemetry-collector-contrib:0.135.0 validate --config=/c.yaml
docker run --rm --entrypoint promtool -v $PWD/prometheus/prometheus.yml:/p.yml prom/prometheus:v3.6.0 check config /p.yml
```

## Conventions

- **Versions explicites** pour toutes les images et dépendances, jamais `latest`. Les versions de
  l'agent (`OTEL_AGENT_VERSION` dans le Dockerfile et `docker-compose.yml`) et de
  `opentelemetry-api` (`pom.xml`) se mettent à jour ensemble : l'API doit rester ≤ celle embarquée
  par l'agent.
- **Aucun secret dans le dépôt** : identifiants via `.env` (ignoré par git), modèle dans
  `.env.example`.
- **PromQL** : vérifier les noms réels sur `:8889/metrics` ou dans Prometheus avant d'écrire une
  requête ; ne pas les deviner. Une montée de version de l'agent ou du Collector peut renommer
  métriques ou labels.
- **Dashboard** : `grafana/tools/gen_dashboard.py` est la source de vérité ; le JSON est généré.
  Les panneaux HTTP excluent `http_route=~"/actuator.*"` (trafic des healthchecks).
- Métriques métier : via l'API OpenTelemetry (`GlobalOpenTelemetry.getMeter("com.example.demo")`),
  nom OTel en `point.separated`, avec unité. Pas de Micrometer.
- Un commit par étape logique, messages en français au format `type(portée): description`.
- Après tout changement, relancer `./scripts/check.sh`.
