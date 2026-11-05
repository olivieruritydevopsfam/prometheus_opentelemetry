# Exercice : une alerte heap qui arrive trop tard

Deuxième exercice sur les règles Prometheus, après celui du seuil 5xx
([regles-prometheus-ci.md](regles-prometheus-ci.md), section 4). On y pratique trois tâches
courantes d'un DevOps :

1. **revoir une alerte mal calée** : la découper en deux paliers, warning puis critical ;
2. **mettre une alerte en silence** pendant qu'on traite l'incident ;
3. **vérifier le retour à la normale**, puis revenir à l'état initial.

Durée : environ 15 minutes. Prérequis : la stack tourne (`docker compose up -d`) et
`./scripts/check.sh` est vert sur la partie *e) Alerting*.

## Le décor en 4 phrases

- La **heap** est la mémoire où une application Java range ses objets. Sa taille maximale
  est fixée au démarrage : ici 512 Mo (`-Xmx512m` dans `docker-compose.yml`).
- Le **GC** (garbage collector) libère régulièrement les objets dont l'application ne se sert
  plus. La courbe de la heap fait donc des dents de scie : elle monte, puis le GC la fait
  redescendre.
- Une **fuite mémoire** se produit quand l'application garde des objets dont elle n'a plus
  besoin. Le GC ne peut pas les libérer, et le bas des dents de scie monte petit à petit.
- Quand la heap est pleine, la JVM passe son temps en GC, ne répond plus, puis s'arrête avec
  une `OutOfMemoryError`.

L'application de démo a deux endpoints pour simuler une fuite. Le trafic automatique ne les
appelle jamais.

| Commande | Effet |
|---|---|
| `curl -XPOST 'localhost:8080/leak?mb=100'` | garde 100 Mo de plus en mémoire, que le GC ne peut pas libérer |
| `curl -XDELETE localhost:8080/leak` | relâche tout et lance un GC : la heap redescend tout de suite |

## Étape 1 : regarder l'état de départ

Ouvrez directement le dashboard :
<http://localhost:3000/d/java-demo-otel/java-demo-e28094-opentelemetry?from=now-15m&to=now>

Pour y aller à la main : menu **Dashboards → OTel Lab → Java demo — OpenTelemetry**. Le login
est `admin`, avec le mot de passe de votre fichier `.env`. Au premier affichage, les panneaux
peuvent rester 20 à 30 s sur « Loading plugin panel... ».

Laissez les filtres *Service* et *Instance* sur **All**. Les deux éléments à regarder sont en
haut de la page :

- **en haut à droite**, le stat *Heap utilisée / max* indique environ 10 à 20 % ;
- **juste en dessous, à gauche** (rangée *JVM*), le graphique *Mémoire heap* montre trois
  courbes : *utilisée* (les dents de scie), *committed* (ce que la JVM a réservé) et *max*
  (512 Mo, soit `512 MiB` dans Grafana).

Le panneau *État des alertes*, utilisé plus loin, est tout en bas de la page, dans la rangée
*Règles et alertes Prometheus*.

La règle actuelle se trouve dans `prometheus/rules/java-demo.rules.yml`.

```yaml
- alert: JavaDemoHeapUsageHigh
  expr: job:jvm_memory_heap_used:ratio > 0.9   # heap utilisée / max, au-dessus de 90 %
  for: 5m                                      # pendant 5 minutes d'affilée
  labels:
    severity: warning
```

`job:jvm_memory_heap_used:ratio` est une *règle d'enregistrement*, définie plus haut dans le
même fichier. Prometheus calcule ce ratio toutes les 15 s et l'enregistre sous ce nom.

## Étape 2 : provoquer la fuite et constater le problème

Lancez la fuite en trois fois (100 + 100 + 50 Mo), en regardant le graphique *Mémoire heap* entre chaque appel :

```bash
curl -XPOST 'localhost:8080/leak?mb=100'
```
```bash
curl -XPOST 'localhost:8080/leak?mb=100'
```
```bash
curl -XPOST 'localhost:8080/leak?mb=50'
```

Après chaque appel, la courbe *utilisée* monte d'une marche. Avec 250 Mo retenus, le stat
*Heap utilisée / max* indique **environ 73 à 77 %**.

> **N'allez pas au-delà de 250 Mo.** Pendant la préparation du lab, avec 300 Mo la heap
> frôlait déjà 90 %, et avec 400 Mo la JVM a cessé de répondre : elle passait tout son temps
> en GC. Si cela vous arrive, lancez `docker compose restart java-demo`.

**Le problème** : les trois quarts de la heap sont pris, la fuite continue (en vrai, elle ne
s'arrête pas d'elle-même), et **aucune alerte ne se déclenche**. Le panneau *État des alertes* reste sur « aucune alerte
active ». Le seuil de 90 % est trop proche de la panne : quand il est atteint, il est
souvent trop tard. Le `for: 5m` retarde encore l'alerte de 5 minutes.

Laissez la fuite en place pour la suite.

## Étape 3 : modifier la règle

La correction habituelle consiste à créer **deux paliers** pour la même alerte :

| Palier | Seuil | Durée | Rôle |
|---|---|---|---|
| warning | > 70 % | 2 min | « quelque chose ne va pas, regarde dans la journée » |
| critical | > 90 % | 2 min | « la panne est imminente, intervenir maintenant » |

C'est le même principe que `JavaDemoHighErrorRate`, qui a déjà deux paliers. Alertmanager
est configuré pour masquer le warning quand le critical de la même alerte est actif
(`inhibit_rules` dans `alertmanager/alertmanager.yml`), pour ne pas recevoir deux messages.

### Option A : à la main, pour comprendre

Dans `prometheus/rules/java-demo.rules.yml`, remplacez le bloc `JavaDemoHeapUsageHigh` par :

```yaml
      - alert: JavaDemoHeapUsageHigh
        expr: job:jvm_memory_heap_used:ratio > 0.9
        for: 2m
        labels:
          severity: critical
          team: lab
        annotations:
          summary: "Heap à {{ $value | humanizePercentage }} sur {{ $labels.instance }}"
          description: "La heap utilisée dépasse 90 % du max (-Xmx) depuis 2 min : OutOfMemoryError imminente."

      - alert: JavaDemoHeapUsageHigh
        expr: job:jvm_memory_heap_used:ratio > 0.7
        for: 2m
        labels:
          severity: warning
          team: lab
        annotations:
          summary: "Heap à {{ $value | humanizePercentage }} sur {{ $labels.instance }}"
          description: "La heap utilisée dépasse 70 % du max (-Xmx) depuis 2 min : fuite mémoire probable ou heap trop petite."
```

Lancez ensuite la validation, comme le ferait la CI :

```bash
./scripts/deploy-rules.sh validate
```

**Elle échoue**, et c'est voulu. Le test existant dans `prometheus/tests/java-demo.rules.test.yml`
simule une heap à 95 % (à ne pas confondre avec le seuil de 90 % de la règle) et attend
exactement un warning, avec l'ancienne description. Avec les deux paliers, cette heap à 95 %
dépasse 70 % et 90 % : la règle produit deux alertes, avec de nouvelles descriptions. Le test
signale ainsi que le comportement a changé. La règle du lab s'applique : une règle modifiée a
besoin de tests à jour. Le plus simple est de prendre la version préparée, qui contient la règle **et** les
tests :

```bash
git checkout -- prometheus/
```
```bash
git apply docs/exercices/heap-paliers.patch
```

### Option B : directement avec le patch

```bash
git apply docs/exercices/heap-paliers.patch
```

Pour voir ce que le patch change :

```bash
git diff prometheus/
```

Il contient deux scénarios de test :

- heap à 80 % : rien après 1 minute, un **warning** après 5 minutes ;
- heap à 95 % : **warning et critical** en même temps.

Puis validez :

```bash
./scripts/deploy-rules.sh validate
```

Résultat attendu : `SUCCESS: 15 rules found`, puis `SUCCESS` pour les tests. Il y a une règle
de plus qu'avant : 15 au lieu de 14.

## Étape 4 : déployer

```bash
./scripts/deploy-rules.sh deploy
```

À la fin, le script affiche « 15 règles chargées, aucune en erreur ».

## Étape 5 : voir l'alerte arriver

La fuite de 250 Mo est toujours là (73 à 77 %), donc le palier warning à 70 % est dépassé.

| Quand | Où regarder | Ce qu'on voit |
|---|---|---|
| ~30 s | Dashboard, panneau *État des alertes* | `JavaDemoHeapUsageHigh warning` en orange : **pending**. La condition est vraie, et Prometheus attend les 2 minutes du `for`. |
| ~2 min 30 s | Même panneau, stat *Alertes firing* | La ligne passe au rouge : **firing**. Le compteur passe à 1. |
| ~2 min 45 s | **Alerting → Active notifications**, *Choose Alertmanager* → **Alertmanager** | Le groupe `JavaDemoHeapUsageHigh` apparaît, avec un résumé du type « Heap à 77% sur … ». |

Le critical (90 %) ne se déclenche pas : on est en dessous. C'est le but recherché. On est
prévenu **avant** la zone dangereuse.

## Étape 6 : mettre l'alerte en silence pendant l'intervention

En vrai, une fois l'alerte reçue, quelqu'un prend l'incident en charge. On pose alors un
**silence** pour que l'alerte ne renvoie pas de notifications pendant l'analyse.

En ligne de commande, l'outil `amtool` est fourni avec Alertmanager :

```bash
docker compose exec alertmanager amtool silence add alertname=JavaDemoHeapUsageHigh --duration=30m --author=devops --comment="Analyse de la fuite mémoire en cours" --alertmanager.url=http://localhost:9093
```

La commande affiche l'identifiant du silence, par exemple
`aaf6f73c-2971-4ac0-aace-bd237c96906c`. Notez-le. Pour lister les silences :

```bash
docker compose exec alertmanager amtool silence query --alertmanager.url=http://localhost:9093
```

Le même silence peut être posé dans l'interface d'Alertmanager (<http://localhost:9093>, bouton
**New Silence**) ou dans Grafana (**Alerting → Silences**, en choisissant l'Alertmanager
*Alertmanager*).

**À bien comprendre** : le silence agit seulement dans **Alertmanager**.

- Dans Prometheus et sur le dashboard, l'alerte reste **firing** en rouge. La condition est
  toujours vraie : le silence ne change pas la réalité.
- Dans Alertmanager, l'alerte passe en **suppressed** : elle n'envoie plus de notification.

Un silence a toujours une durée et un commentaire. Sinon, on oublie de le retirer et on rate
la prochaine vraie panne.

## Étape 7 : corriger l'incident et vérifier le retour à la normale

On « corrige la fuite », comme le ferait le correctif d'un développeur ou un redémarrage :

```bash
curl -XDELETE localhost:8080/leak
```

- Le graphique *Mémoire heap* redescend aussitôt. Le stat revient à environ 10 à 20 %.
- Dans les 15 à 30 s, l'alerte disparaît de Prometheus. Le panneau *État des alertes*
  repasse à « aucune alerte active ».
- Alertmanager marque l'alerte comme résolue.

Retirez ensuite le silence, sans attendre ses 30 minutes. Remplacez `<id>` par l'identifiant
noté à l'étape 6 :

```bash
docker compose exec alertmanager amtool silence expire <id> --alertmanager.url=http://localhost:9093
```

## Étape 8 : revenir à l'état initial

```bash
git checkout -- prometheus/
```
```bash
./scripts/deploy-rules.sh deploy
```
```bash
./scripts/check.sh
```

Le script repasse à « 14 règles chargées ». Dans `check.sh`, la section *e) Alerting* est
entièrement OK.

> Ce changement de paliers est une vraie amélioration. Dans une équipe, on le garderait :
> on ferait un commit, puis une merge request, et la CI le déploierait. On revient en arrière
> ici seulement pour pouvoir refaire l'exercice.

## Pour aller plus loin : les tâches courantes sur les règles

| Tâche | Exemple dans le lab | Fichier touché |
|---|---|---|
| Ajuster un seuil trop sensible ou pas assez | Exercice 5xx (10 % → 2 %) | `prometheus/rules/*.yml` + tests |
| Ajouter un palier ou une sévérité | Cet exercice (warning 70 % / critical 90 %) | `prometheus/rules/*.yml` + tests |
| Ajuster le `for` (patience avant l'alerte) | `for: 5m` → `2m` ici | `prometheus/rules/*.yml` + tests |
| Mettre en silence pendant une intervention | Étape 6 | Aucun fichier : action dans Alertmanager |
| Éviter les alertes en double | `inhibit_rules` (critical masque warning) | `alertmanager/alertmanager.yml` |
| Envoyer à la bonne équipe (Slack, e-mail…) | `route` / `receivers` (ici `lab-null`, qui n'envoie rien) | `alertmanager/alertmanager.yml` |
| Créer une nouvelle alerte | Copier une règle existante et adapter `expr`, puis écrire son test | `prometheus/rules/*.yml` + tests |

Avant d'écrire une nouvelle expression, vérifiez toujours le nom réel de la métrique dans
Prometheus (<http://localhost:9090>, onglet *Query*) ou sur <http://localhost:8889/metrics>.
Une métrique mal orthographiée ne produit pas d'erreur : l'alerte ne se déclenche jamais, en
silence.

## En cas de souci

| Symptôme | Cause probable | Solution |
|---|---|---|
| `curl` sur `/leak` renvoie une erreur 500 | `OutOfMemoryError` : trop de mémoire retenue | `curl -XDELETE localhost:8080/leak`, ou `docker compose restart java-demo` |
| L'app ne répond plus après la fuite | heap pleine, la JVM ne fait plus que du GC | `docker compose restart java-demo` |
| La heap ne monte pas dans Grafana | le dashboard affiche une autre instance (l'app a redémarré) | attendre 1 min ; l'ancienne instance disparaît au bout de 5 min |
| Pas de pending après le déploiement | heap sous 70 % (fuite relâchée ou app redémarrée) | relancer les trois `curl -XPOST` de l'étape 2 |
| `validate` échoue après le patch | le patch a été appliqué sur une règle déjà modifiée | `git checkout -- prometheus/` puis `git apply` à nouveau |
