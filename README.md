# detectBot / mcwatch

Surveille trois annuaires de serveurs Minecraft et prévient dès qu'un **nouveau
serveur** y est référencé. Un passage par heure, notification push via
[ntfy](https://ntfy.sh).

L'idée : sur ces sites, chaque serveur reçoit un identifiant numérique
incrémental. Il suffit donc de tester les quelques IDs suivant le dernier connu
pour savoir si de nouveaux serveurs sont apparus.

## Sites surveillés et détection

| Site | URL | ID valide | ID jamais attribué | ID supprimé |
|---|---|---|---|---|
| serveursminecraft.org | `/serveur/{id}` | `200` | `302` vers `/` | `302` vers `/` |
| serveur-minecraft.com | `/{id}` | `200` | `404` | — |
| serveur-minecraft-vote.fr | `/serveur/{id}` | `200` | `404` | `301` vers `/` |

Attention au piège : tester `status == 200` ne suffit pas. `serveur-minecraft-vote.fr`
renvoie bien `404` pour un ID jamais attribué, **mais `200` après redirection vers
l'accueil** pour un serveur supprimé — c'est le cas des IDs 2788 et 2789, qui
seraient remontés comme de faux nouveaux serveurs.

La règle est donc la même pour les trois sites :

> le serveur existe **⇔** `HTTP 200` **et** l'URL finale est toujours une fiche
> serveur (`^/serveur/\d+` ou `^/\d+`).

Ça couvre d'un coup les trois façons de dire « ça n'existe pas » : un 404, une
redirection vers l'accueil, ou un 200 sur une page générique.

Sur serveur-minecraft-vote.fr le slug est facultatif : `/serveur/2787` redirige
tout seul vers `/serveur/2787/craft4fight`. Pas besoin de deviner le nom.

Le nom du serveur est extrait du `<title>` de la page — le point le plus stable
d'un site (bien plus qu'un sélecteur CSS).

## Cloudflare

`serveur-minecraft.com` est protégé par un **managed challenge Cloudflare** sur
tout le domaine, y compris `/robots.txt`. Testé et confirmé : ni une requête
HTTP classique, ni l'imitation d'empreinte TLS (`curl_cffi` en mode Chrome) ne
passent — Cloudflare renvoie `403 Just a moment…`. Seul un moteur qui exécute
le JavaScript du challenge y arrive.

D'où le repli navigateur (`network.browser_fallback = "auto"`) : mcwatch tente
d'abord une requête HTTP légère, et ne démarre Chromium headless que pour les
domaines qui l'exigent. Chromium n'est donc jamais lancé pour les deux autres
sites.

Selon l'IP de ton VPS, le challenge peut ne pas se déclencher du tout — les
règles Cloudflare dépendent de la réputation de l'adresse. Commence sans
Playwright : `mcwatch selftest` te dira en une commande si tu en as besoin.

> **Non vérifié de bout en bout.** Le code du repli navigateur
> (`mcwatch/browser.py`) n'a pas pu être testé contre le vrai challenge : la
> machine de développement ne pouvait pas télécharger Chromium. Les deux autres
> sites, eux, sont validés en conditions réelles. Si `selftest` échoue encore
> après l'installation de Playwright, c'est ce fichier qu'il faut ajuster
> (durée d'attente, arguments de lancement).

## Installation sur le VPS

Prérequis : Python 3.9+ (3.11+ recommandé), `git`, `python3-venv`.

```bash
git clone <ton-repo> /opt/detectBot
cd /opt/detectBot

# 1er passage : crée le venv, installe les dépendances et génère config.toml
sudo ./deploy/install.sh

# Renseigne ton topic ntfy
sudo nano /opt/detectBot/config.toml

# 2e passage : initialise l'état et arme le timer systemd
sudo ./deploy/install.sh
```

Vérifie que la détection fonctionne depuis cette machine :

```bash
.venv/bin/python -m mcwatch selftest
```

Si `serveur-minecraft.com` remonte `CHALLENGE`, ajoute le navigateur :

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install --with-deps chromium   # ~200 Mo
.venv/bin/python -m mcwatch selftest                # doit passer au vert
```

## Recevoir les notifications

1. Installe l'app **ntfy** ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)
   / [iOS](https://apps.apple.com/us/app/ntfy/id1625396347)), ou ouvre
   `https://ntfy.sh/<ton-topic>` dans un navigateur.
2. Abonne-toi au topic défini dans `config.toml`.
3. Teste : `.venv/bin/python -m mcwatch test-notify`

> Un topic ntfy.sh sans compte est **public** : n'importe qui connaissant son
> nom peut le lire. Le contenu n'étant que des URLs déjà publiques, ce n'est pas
> un problème ici — garde simplement un nom imprévisible. Pour un topic
> réellement privé, crée un compte ntfy.sh et renseigne `ntfy.token`.

## Commandes

```bash
python -m mcwatch run              # un passage (ce que lance le timer)
python -m mcwatch run --dry-run    # sonde sans rien écrire ni notifier
python -m mcwatch status           # curseurs, dernier succès, erreurs
python -m mcwatch init             # curseurs aux derniers IDs connus
python -m mcwatch set-cursor serveur_minecraft_com 5907
python -m mcwatch test-notify      # vérifie la chaîne de notification
python -m mcwatch selftest         # vérifie la détection sur des IDs connus
```

## Comment fonctionne un passage

Pour chaque site, avec un curseur à `N` et une fenêtre de 3 :

1. On teste `N+1`, `N+2`, `N+3` (pause aléatoire de 1 à 3 s entre chaque).
2. Le curseur avance tant que les IDs sont **contigus** : si `N+1` et `N+2`
   existent mais pas `N+3`, le curseur passe à `N+2`.
3. Tout ID trouvé **et pas déjà notifié** déclenche une notification — y compris
   au-delà d'un trou.
4. **Gestion des trous** : si `N+1` n'existe pas mais `N+3` oui (serveur
   supprimé, ID définitivement perdu), le curseur est bloqué. Après
   `gap_skip_after_runs` passages (6 par défaut, soit 6 h), il saute
   par-dessus le trou. Cas réel au moment de l'écriture : sur
   serveur-minecraft-vote.fr, 2788 et 2789 sont supprimés et 2790 existe.
5. En cas d'erreur réseau ou de challenge, **le curseur ne bouge pas** : on
   préfère re-sonder que rater un serveur. Une alerte part au 1er échec, puis
   toutes les 24 erreurs consécutives.

Charge totale : 9 requêtes par heure, réparties sur 3 domaines, avec un décalage
aléatoire allant jusqu'à 10 minutes après l'heure ronde.

## Fichiers

```
mcwatch/
  __main__.py   CLI (run, status, init, set-cursor, test-notify, selftest)
  scanner.py    logique de scan, avancement du curseur, notifications
  sites.py      définition des 3 sites et de leur détection
  session.py    aiguillage httpx / navigateur
  fetcher.py    client HTTP et détection des challenges
  browser.py    repli Chromium headless (Playwright, optionnel)
  state.py      persistance JSON atomique
  notify.py     envoi ntfy
  config.py     chargement config.toml + variables d'environnement
deploy/
  install.sh        installation VPS (venv + systemd)
  mcwatch.service   unité oneshot durcie
  mcwatch.timer     déclenchement horaire
config.example.toml
```

`config.toml` et `state.json` sont ignorés par git : ils contiennent
respectivement ton topic ntfy et l'état de progression.

## Exploitation

```bash
systemctl list-timers mcwatch.timer      # prochain déclenchement
systemctl start mcwatch.service          # forcer un passage
journalctl -u mcwatch.service -n 50      # logs du dernier passage
journalctl -u mcwatch.service -f         # suivre en direct
```

## Si un site change

Le point de rupture le plus probable est le **format du `<title>`** (le nom du
serveur remonterait bizarrement) ou un changement de comportement sur les IDs
inconnus. Les deux se voient immédiatement avec `mcwatch selftest`, et se
corrigent dans `mcwatch/sites.py` — un seul fichier, une expression régulière
par site.
