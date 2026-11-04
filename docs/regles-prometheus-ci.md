# Changer une règle Prometheus et la déployer depuis une CI

Ce guide explique comment Prometheus charge ses règles, comment automatiser leur déploiement
depuis une CI/CD (Bamboo en exemple), puis déroule un exercice complet. On y modifie un seuil
d'alerte, la CI bloque le premier essai, on corrige, on déploie, et on voit l'effet dans Grafana.

## 1. Comment Prometheus charge ses règles

Les règles sont de **simples fichiers YAML**. `prometheus.yml` indique où les trouver :

```yaml
rule_files:
  - /etc/prometheus/rules/*.yml
```

Prometheus lit ces fichiers **au démarrage** et **à chaque rechargement**. Il ne surveille pas
le dossier : déposer un nouveau fichier ne suffit pas, il faut demander un rechargement.
Trois façons de le faire :

| Méthode | Commande | Remarque |
|---|---|---|
| API HTTP | `curl -XPOST http://localhost:9090/-/reload` | Nécessite `--web.enable-lifecycle` (activé dans le lab). Renvoie 200 ou 500 avec le motif de l'erreur : c'est la méthode à utiliser en CI. |
| Signal | `docker compose kill -s HUP prometheus` (ou `kill -HUP <pid>`) | Pas de code retour exploitable : il faut vérifier après coup. |
| Automatique | `--config.auto-reload-interval=30s` | Prometheus relit périodiquement sa configuration quand elle change. Pratique, mais la CI ne sait pas si le rechargement a réussi. |

Points importants :

- **Tout ou rien.** Si un seul fichier est invalide, Prometheus refuse tout le rechargement et
  **continue avec les anciennes règles**. Une erreur ne coupe donc pas l'alerting, mais la
  nouvelle règle n'est pas appliquée. L'API renvoie HTTP 500 avec le message d'erreur.
- **Effet immédiat.** Les règles sont réévaluées au cycle suivant (`evaluation_interval`, 15 s).
  Une alerte dont l'expression change repart de l'état *inactive*, puis passe par *pending*
  pendant la durée `for` avant d'être *firing*.
- **Pas de redémarrage.** Les données déjà stockées ne sont pas touchées.
- **Vérifier le résultat.** `GET /api/v1/status/runtimeinfo` renvoie `reloadConfigSuccess`
  en temps réel. La métrique `prometheus_config_last_reload_successful` ne change qu'au scrape
  suivant : juste après un échec, elle peut encore valoir 1.

## 2. Le circuit en CI/CD

```
 git push ──▶ CI : validate ──▶ CI : deploy ───────────────────────────▶ Grafana
              promtool check     copie des fichiers (rsync / Ansible)    dashboard, Alerting
              promtool test      POST /-/reload
                                 vérification /api/v1/rules
```

1. **Valider** sur toutes les branches : `promtool check rules` (syntaxe et PromQL), puis
   `promtool test rules` (scénarios de `prometheus/tests/`). Une erreur arrête tout et rien
   n'est déployé.
2. **Copier** les fichiers là où Prometheus les lit. Le moyen dépend de l'installation :
   rsync/scp vers une VM, rôle Ansible, ConfigMap Kubernetes, ressource `PrometheusRule` avec
   le Prometheus Operator…
3. **Recharger** via `POST /-/reload`, et faire échouer le build si la réponse n'est pas 200.
4. **Vérifier** que Prometheus a chargé le bon nombre de règles et qu'aucune n'est en erreur.

Ces quatre étapes sont dans [`scripts/deploy-rules.sh`](../scripts/deploy-rules.sh). Le script
s'utilise en local comme en CI, ce qui permet de tester un changement avant de pousser :

```bash
./scripts/deploy-rules.sh validate   # étape 1 seulement
./scripts/deploy-rules.sh deploy     # étapes 1 à 4
```

| Variable | Défaut | Rôle |
|---|---|---|
| `PROM_URL` | `http://localhost:9090` | Prometheus à recharger |
| `RULES_DEST` | vide | Destination rsync : `/chemin/local/` ou `user@hôte:/etc/prometheus/rules/`. Vide = fichiers déjà en place (cas du lab : `prometheus/rules` est monté dans le conteneur). |

`promtool` tourne dans l'image `prom/prometheus:v3.6.0`, la même version que le serveur. Sur
un agent sans Docker, le script prend le binaire `promtool` du `PATH`.

## 3. Mise en place dans Bamboo

Le plan est décrit dans [`bamboo-specs/bamboo.yml`](../bamboo-specs/bamboo.yml) (Bamboo Specs
en YAML). Il a deux stages :

- **Valider** : sur chaque commit, toutes branches. `./scripts/deploy-rules.sh validate`.
- **Déployer** : bouton manuel, exécuté seulement sur `main`. `./scripts/deploy-rules.sh deploy`.

Pour l'activer :

1. Dans Bamboo, créez (ou choisissez) le projet de clé `OBS`. Ajoutez le dépôt Git comme
   *linked repository* et activez **Bamboo Specs** sur ce dépôt (onglet *Bamboo Specs* du
   dépôt, autorisation d'accès au projet). Bamboo crée ou met à jour le plan à chaque push du
   fichier `bamboo-specs/bamboo.yml`.
2. L'agent qui exécute le plan doit avoir Docker (capability `system.docker.executable`),
   ou `promtool` installé.
3. Renseignez les variables du plan :
   - `prom_url` : l'URL de Prometheus vue depuis l'agent ;
   - `rules_dest` : la destination des fichiers. Pour une VM, ajoutez à l'agent une clé SSH
     autorisée sur l'hôte Prometheus.
   Dans les scripts, Bamboo les expose en `bamboo_prom_url` et `bamboo_rules_dest`.
4. Sur la machine Prometheus, protégez `/-/reload` : toute personne qui atteint le port peut
   recharger ou arrêter Prometheus avec `--web.enable-lifecycle`. Placez un reverse proxy avec
   authentification, ou limitez l'accès réseau à l'agent CI.

Pour un vrai déploiement multi-environnements (lab, recette, prod), Bamboo propose aussi les
*Deployment projects* : le plan valide et produit un artefact contenant `prometheus/rules/`, et
chaque environnement lance `deploy-rules.sh deploy` avec ses propres variables. La logique du
script ne change pas.

> Le fichier Bamboo Specs n'a pas été exécuté sur un serveur Bamboo. Le script qu'il appelle a,
> lui, été testé sur le lab (déploiement, rejet d'un fichier invalide, rejet par les tests).

## 4. Exercice : changer un seuil et le voir dans Grafana

Objectif : abaisser le seuil *warning* de `JavaDemoHighErrorRate` de 10 % à 2 %. Le régime
normal du load-generator est d'environ 5 % de 5xx, donc l'alerte va se déclencher. Pendant
l'exercice, `check.sh` affichera « alertes actives » en KO, c'est attendu.

### Étape 1 : regarder l'état de départ dans Grafana

Ouvrez <http://localhost:3000>, dashboard **Java demo — OpenTelemetry**, rangée
**Règles et alertes Prometheus** (en bas) :

- *Règles chargées* : 14 ;
- *Dernier rechargement* (il y a combien de temps) et *Rechargement* (réussi ou refusé) ;
- *Alertes firing* : 0 ;
- *État des alertes* : « aucune alerte active » ;
- *Taux de 5xx évalué par la règle d'enregistrement* : autour de 5 %.

Ouvrez aussi **Alerting → Alert rules**
(<http://localhost:3000/alerting/list?search=JavaDemoHighErrorRate>). Grafana y liste les
règles de Prometheus dans la section *Data source-managed*, sous
`/etc/prometheus/rules/java-demo.rules.yml › java-demo.alerts`. Dépliez la règle
`JavaDemoHighErrorRate` dont le résumé commence par « Taux de 5xx élevé » : le champ
*Expression* contient `> 0.1`.

### Étape 2 : modifier la règle seule, et constater que la CI bloque

Dans `prometheus/rules/java-demo.rules.yml`, règle `JavaDemoHighErrorRate` de sévérité
`warning`, changez :

```yaml
        expr: job:http_server_requests_5xx:ratio5m > 0.10
```

en

```yaml
        expr: job:http_server_requests_5xx:ratio5m > 0.02
```

Puis lancez la validation, comme le ferait la CI :

```bash
./scripts/deploy-rules.sh validate
```

Le test du scénario « régime normal » échoue. Il attend zéro alerte à 5 % de 5xx, et la
nouvelle règle en produit une :

```
== Tests unitaires des règles
  FAILED:
    alertname: JavaDemoHighErrorRate, time: 10m,
        exp:[],
        got:[ ... severity="warning" ... summary="Taux de 5xx élevé sur java-demo : 5%" ]
ÉCHEC tests en échec : rien n'a été déployé
```

C'est le rôle des tests : un seuil qui ferait sonner l'alerte en permanence ne part pas en
production par accident. Dans Bamboo, le stage *Valider* serait rouge et le stage *Déployer*
ne serait pas proposé.

### Étape 3 : faire le changement complet

Un changement de règle s'accompagne de ses tests et de son texte. Pour l'exercice, il faut :

- mettre la description de l'alerte à jour (« Plus de 2 % ») ;
- dans `prometheus/tests/java-demo.rules.test.yml`, attendre une alerte warning dans le
  scénario « régime normal » ;
- mettre à jour la description attendue dans les deux autres scénarios.

Le changement complet est fourni sous forme de patch. Il remplace aussi la modification faite
à l'étape 2 :

```bash
git checkout -- prometheus/
git apply docs/exercices/seuil-5xx.patch
git diff --stat                       # 2 fichiers : la règle et ses tests
./scripts/deploy-rules.sh validate    # SUCCESS
```

Pour un vrai changement, on ferait ensuite un commit sur une branche, une pull request, puis
un merge sur `main`. Bamboo validerait la branche, puis le déploiement sur `main`.

### Étape 4 : déployer

```bash
./scripts/deploy-rules.sh deploy
```

La sortie liste les règles chargées par Prometheus. On y lit la nouvelle expression :

```
== Rechargement de http://localhost:9090
rechargement accepté
== Vérification des règles chargées
  alerte JavaDemoHighErrorRate      job:http_server_requests_5xx:ratio5m > 0.25
  alerte JavaDemoHighErrorRate      job:http_server_requests_5xx:ratio5m > 0.02
14 règles chargées, aucune en erreur
```

### Étape 5 : constater le changement dans Grafana

Le dashboard se rafraîchit toutes les 10 s.

| Quand | Où regarder | Ce qu'on voit |
|---|---|---|
| Tout de suite | Rangée *Règles et alertes Prometheus* | *Dernier rechargement* indique « a few seconds ago » (après le scrape suivant, ≤ 15 s). |
| Tout de suite | **Alerting → Alert rules**, *Data source-managed* (rechargez la page) | La règle warning affiche *Expression* `… > 0.02` et la nouvelle description « Plus de 2 % ». |
| ~15 s | Panneau *État des alertes* | La ligne `JavaDemoHighErrorRate warning` apparaît en orange : **pending**. |
| ~2 min 15 s | Panneau *État des alertes*, stat *Alertes firing* | La ligne devient rouge : **firing**. Le compteur passe à 1 et devient rouge. Dans *Alert rules*, la règle affiche *Firing*. |
| ~2 min 30 s | **Alerting → Active notifications**, puis *Choose Alertmanager* → **Alertmanager** (ou <http://localhost:9093>) | Un groupe `alertname JavaDemoHighErrorRate`, « 1 alert: 1 active », délivré au receiver `lab-null`. Le résumé donne la valeur, par exemple « Taux de 5xx élevé sur java-demo : 5.994% ». |

Le délai de 2 minutes est le `for: 2m` de la règle : la condition doit rester vraie pendant
2 minutes avant que l'alerte soit envoyée.

### Étape 6 : revenir à l'état initial

```bash
git checkout -- prometheus/
./scripts/deploy-rules.sh deploy
./scripts/check.sh                    # « aucune alerte active » redevient OK (après ~15 s)
```

Dans Grafana, l'expression repasse à `> 0.1`. Dans le panneau *État des alertes*, la ligne de
l'alerte s'arrête (elle reste visible dans l'historique) et « aucune alerte active » reprend.
Alertmanager marque l'alerte comme résolue.

## 5. Dépannage

| Symptôme | Cause probable | Que faire |
|---|---|---|
| `rechargement refusé (HTTP 500)` | Fichier invalide côté serveur, ou chemin `rule_files` introuvable | Le message de Prometheus est affiché. `docker compose logs prometheus` donne le détail. Les anciennes règles restent actives. |
| `Prometheus injoignable` | Mauvais `PROM_URL`, ou réseau entre l'agent CI et Prometheus | `curl $PROM_URL/-/ready` depuis l'agent. |
| `HTTP 403` sur `/-/reload` | `--web.enable-lifecycle` absent | Ajouter le flag au lancement de Prometheus. |
| `N règles chargées, M attendues` | Fichiers pas copiés, ou copiés ailleurs que dans le dossier lu par `rule_files` | Vérifier `RULES_DEST` et le motif `rule_files` de `prometheus.yml`. |
| La règle a changé mais Grafana montre l'ancienne | Cache de la page | Recharger la page *Alert rules*. Le dashboard, lui, suit en 10 s. |

## 6. Et en dehors du lab

- **Kubernetes avec Prometheus Operator** : les règles sont des ressources `PrometheusRule`.
  La CI fait un `kubectl apply` (ou passe par Helm/Argo CD), et l'opérateur génère la
  configuration puis recharge Prometheus. `promtool check/test` reste l'étape de validation.
- **Grafana Mimir, Cortex ou Thanos Ruler** : les règles sont envoyées par API avec
  `mimirtool rules sync` (ou `cortextool`). Il n'y a pas de fichier à copier ni de
  rechargement à demander.
- **Alertes gérées par Grafana** : une autre approche, où Grafana évalue lui-même les règles.
  Elles se déploient par provisioning de fichiers ou par Terraform. Le lab ne l'utilise pas :
  les règles restent dans Prometheus, versionnées et testées avec `promtool`.
