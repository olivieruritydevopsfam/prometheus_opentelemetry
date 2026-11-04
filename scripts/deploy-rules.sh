#!/usr/bin/env bash
# Valide, déploie et recharge les règles Prometheus (prometheus/rules/*.yml).
# Même script en local et en CI (Bamboo, GitLab...) : voir docs/regles-prometheus-ci.md.
#
# Usage : ./scripts/deploy-rules.sh validate   # promtool : syntaxe + tests unitaires
#         ./scripts/deploy-rules.sh deploy     # validate, copie, rechargement, vérification
#
# Variables :
#   PROM_URL    Prometheus à recharger (défaut http://localhost:9090)
#   RULES_DEST  destination rsync des règles : chemin local ou user@hôte:/etc/prometheus/rules/.
#               Vide = fichiers déjà en place (lab : prometheus/rules est monté dans le conteneur).
set -euo pipefail
cd "$(dirname "$0")/.."

PROM_URL=${PROM_URL:-http://localhost:9090}
PROM_IMAGE=prom/prometheus:v3.6.0   # même version que docker-compose.yml

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
die()  { printf '\033[31mÉCHEC\033[0m %s\n' "$1" >&2; exit 1; }

# promtool de l'image épinglée (même version que le serveur) ; binaire local en repli
promtool() {
  if docker info >/dev/null 2>&1; then
    docker run --rm --entrypoint promtool -v "$PWD/prometheus:/p:ro" -w /p "$PROM_IMAGE" "$@"
  elif command -v promtool >/dev/null; then
    (cd prometheus && command promtool "$@")
  else
    die "ni Docker ni promtool disponibles sur cet agent"
  fi
}

validate() {
  local rules tests
  rules=$(cd prometheus && ls rules/*.yml)
  tests=$(cd prometheus && ls tests/*.yml)
  step "Syntaxe et PromQL des règles"
  # shellcheck disable=SC2086
  promtool check rules $rules || die "règles invalides"
  step "Tests unitaires des règles"
  # shellcheck disable=SC2086
  promtool test rules $tests || die "tests en échec : rien n'a été déployé"
}

reload() {
  step "Rechargement de $PROM_URL"
  local out code
  out=$(curl -sS -XPOST -w '\n%{http_code}' "$PROM_URL/-/reload") || die "Prometheus injoignable"
  code=${out##*$'\n'}
  # En cas d'erreur Prometheus garde l'ancienne configuration et renvoie le motif
  [[ $code == 200 ]] || die "rechargement refusé (HTTP $code) : ${out%$'\n'*}"
  echo "rechargement accepté"
}

verify() {
  step "Vérification des règles chargées"
  # runtimeinfo est à jour immédiatement, contrairement à la métrique
  # prometheus_config_last_reload_successful qui n'est relue qu'au scrape suivant
  curl -s "$PROM_URL/api/v1/status/runtimeinfo" | grep -q '"reloadConfigSuccess":true' \
    || die "le dernier rechargement de Prometheus a échoué"
  local expected
  expected=$(cat prometheus/rules/*.yml | grep -cE '^[[:space:]]*- (alert|record):')
  curl -s "$PROM_URL/api/v1/rules" | EXPECTED=$expected python3 -c '
import json, os, sys
groups = json.load(sys.stdin)["data"]["groups"]
rules = [r for g in groups for r in g["rules"]]
for r in rules:
    kind = "alerte" if r["type"] == "alerting" else "record"
    print("  %-6s %-52s %s" % (kind, r["name"], " ".join(r["query"].split())[:70]))
bad = [r["name"] for r in rules if r["health"] == "err"]
exp = int(os.environ["EXPECTED"])
if len(rules) != exp:
    sys.exit(f"{len(rules)} règles chargées, {exp} attendues dans prometheus/rules")
if bad:
    sys.exit("règles en erreur : " + ", ".join(bad))
print(f"{len(rules)} règles chargées, aucune en erreur")' || die "état des règles inattendu"
}

case ${1:-} in
  validate)
    validate ;;
  deploy)
    validate
    if [[ -n ${RULES_DEST:-} ]]; then
      step "Copie vers $RULES_DEST"
      # --delete : une règle supprimée du dépôt disparaît aussi de la cible
      rsync -av --delete --include='*.yml' --exclude='*' prometheus/rules/ "$RULES_DEST"
    fi
    reload
    verify ;;
  *)
    echo "usage : $0 validate|deploy" >&2; exit 2 ;;
esac
