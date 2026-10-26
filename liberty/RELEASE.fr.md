# Liberty Next — Notes de version

## 7.0.58 — non publiée

**Installation / mise à jour**
- **Corrigé : l'image Docker ne pouvait pas ouvrir sa base par défaut.** Le pilote `aiosqlite`, celui
  du pool `sqlite+aiosqlite://` fourni dans la configuration par défaut et utilisé par la disposition
  Light, n'était installé qu'en développement depuis la première version. Le serveur démarrait quand
  même (le backend d'authentification TOML n'a pas besoin de base), mais les paquets de changements,
  les vues enregistrées et nomaflow restaient désactivés sans message, et `/ready` répondait 503 sur
  toute installation Light. Le pilote fait maintenant partie des dépendances de l'image.
- **Corrigé : une installation qui perdait toute sa configuration après un changement de TLS ou une
  mise à jour.** Relancer `install.sh` (la méthode documentée pour activer le TLS) ou exécuter
  `docker compose -f docker-compose.full.yml pull` recalculait la liste des fichiers compose à partir
  des options de la commande en cours : les fichiers TLS et apps disparaissaient, le conteneur
  redémarrait sans le montage `/apps` et recréait les fichiers de configuration vides. Les données
  n'étaient pas perdues, mais l'installation paraissait réinitialisée. `install.sh` réutilise
  maintenant la valeur `COMPOSE_FILE` enregistrée dans `.env` ; seule l'option `--ssl` remplace le
  fichier TLS.
- **Nouvelle commande `release/liberty`**, point d'entrée unique d'une installation Docker :
  `install`, `start`, `stop`, `restart`, `status`, `doctor`, `ssl letsencrypt|provided|off`,
  `apps <wheel>`, `upgrade [--tag] [--apps]`, `backup`, `restore <dossier>`, `logs`, `migrate`. La
  liste des fichiers compose est enregistrée dans `.env` ; seules les commandes `ssl` et `apps` la
  modifient. `doctor` affiche ce qui ne va pas et la correction à appliquer ; `upgrade` enchaîne le
  diagnostic, une sauvegarde obligatoire, le téléchargement de l'image, le redémarrage, les migrations
  de schéma et l'état final.
- **Nouvelle commande `liberty-admin migrate [--dry-run]`** : applique, ou liste avec `--dry-run`, les
  révisions Alembic en attente de chaque application (`plugins/<app>/db`), et les enregistre dans
  Paramètres → Historique → Mises à jour. Elle remplace le lancement manuel de la tâche
  `nomasx1-upgrade-schema-1` après une mise à jour des applications.
- `liberty-admin` vérifie maintenant la clé de licence comme le serveur ; auparavant, les commandes
  d'administration ignoraient sans message les connecteurs sous licence.

## 7.0.57 — 2026-10-26

**Rechargement**
- **Corrigé : un rechargement de la configuration laissait certains sous-systèmes sur l'ancien
  registre de pools.** Les paquets de changements, les vues de grille enregistrées, la base de
  nomaflow et (sur le chemin du watcher de fichiers) le backend d'authentification conservaient le
  registre d'avant le rechargement ; comme un registre libéré reconstruisait silencieusement ses
  moteurs à partir de son ancienne configuration, ils continuaient avec les anciens identifiants de
  pool jusqu'au redémarrage. Tous les chemins de rechargement (Paramètres → Recharger, le watcher,
  une rotation de licence) passent désormais par un `rebuild_runtime` unique qui repointe chaque
  composant et ferme définitivement l'ancien registre.
- Le framework démarre même si la base par défaut est injoignable au démarrage (nomaflow est
  désactivé pour cette exécution avec un avertissement au lieu de bloquer toute l'application).
  Nouvelle sonde de disponibilité `GET /ready` : `SELECT 1` sur le pool par défaut, 503 en cas
  d'échec ; `/health` reste une simple sonde de vie.

**Sécurité**
- **Les erreurs base de données ne renvoient plus l'instruction SQL ni les valeurs liées** aux
  utilisateurs ordinaires : ils reçoivent le message du pilote et un `error_id` ; les
  superutilisateurs gardent le texte complet. L'instruction et les valeurs ne sont écrites dans le
  journal serveur que si `debug_sql` est activé sur le pool.
- **Rotation des jetons de rafraîchissement.** Chaque `/auth/refresh` renvoie un nouveau jeton et
  consomme celui présenté ; le représenter plus de `[auth] refresh_reuse_grace` secondes après
  (60 s par défaut — deux onglets utilisant le même jeton stocké) est refusé. Les clients API
  doivent conserver le jeton de chaque réponse (l'application web le fait déjà).
- **Limitation des connexions** : après `[auth] login_max_failures` échecs pour un identifiant dans
  `login_window_seconds` (10 en 5 min par défaut ; 5× par adresse cliente), `/auth/login` répond
  429 avec `Retry-After`. `login_max_failures = 0` désactive la limitation.
- Les JWT sont signés via PyJWT (python-jose, plus maintenu, est retiré — mêmes claims et même
  secret, les sessions existantes restent valides). Un `LIBERTY_JWT_SECRET` de moins de 32 octets
  est signalé une fois au démarrage.
- `/info` anonyme ne renvoie que des compteurs et des indicateurs ; les noms de connecteurs / pools,
  l'emplacement du magasin d'authentification et le modèle IA sont réservés aux appels
  authentifiés.
- `POST /admin/import-package` : limite de 50 Mo à l'envoi et garde de 200 Mo décompressés.

**Installation**
- **Extras optionnels.** WeasyPrint (rapports PDF), ldap3 (l'étape `ldap_sync`) et Alembic +
  psycopg2 (les migrations de schéma des applications) ne sont plus des dépendances de base :
  installez `liberty-next[all]` (ou `[reports]` / `[ldap]` / `[alembic]`). L'image Docker embarque
  tous les extras, sans changement. Une installation de base démarre sans Pango ; une demande de
  PDF répond alors 503 avec l'indication d'installation, les rapports markdown continuent de
  fonctionner.
- **Corrigé : l'envoi d'un paquet (Package & Deploy) ne fonctionnait pas hors d'un venv disposant
  par hasard de `python-multipart`** — la dépendance est déclarée, et le contrôle qui rejetait
  chaque fichier réel (« missing 'package' file part ») est corrigé.

**Application web**
- **Premier chargement plus rapide** : l'éditeur Monaco a quitté le bundle de démarrage (816 →
  226 ko gzippés avant l'affichage de la page de connexion) ; il se charge à la première ouverture
  d'un éditeur SQL.
- Une erreur d'affichage dans un onglet ne vide plus toute l'application : l'onglet affiche
  l'erreur avec un bouton Recharger et les autres onglets continuent de fonctionner.
- La réponse de l'assistant IA peut être **interrompue** en cours de flux ; ce qui a été reçu est
  conservé.
- Le bouton Assistant IA suit le thème (variante claire correcte).

**Documentation**
- `CLAUDE.md` est désormais une carte concise du code ; le récit complet de la construction est
  déplacé dans `docs/history/`.

## 7.0.56 — 2026-10-26

**Connecteurs**
- **Corrigé :** un rechargement à chaud de `connectors.toml` / `dictionary.toml` reconstruisait les
  pools Oracle en mode *thin* (le watcher ne transmettait pas `[app] oracle_thick` au registre
  reconstruit) : les connexions en chiffrement réseau natif (NNE) échouaient après toute modification
  de configuration, jusqu'au redémarrage suivant.

**Build, publication et tests**
- **L'intégration continue conditionne chaque publication.** Un nouveau workflow CI exécute la suite
  de tests backend ainsi que la vérification TypeScript + le build de production du frontend à chaque
  push sur `develop` et à chaque pull request ; le workflow de publication l'attend désormais, rien
  n'atteint PyPI ni le registre Docker en échec.
- **Les notes de version sont obligatoires.** Une publication s'arrête avec une erreur explicite
  lorsque `RELEASE.md` n'a pas de section pour la version publiée (le comportement précédent insérait
  un espace réservé vide — la 7.0.55 est sortie ainsi). Le tag git est maintenant créé *avant* la
  publication sur PyPI.
- **Fichier de verrouillage frontend réparé.** `package-lock.json` contenait une entrée `yaml`
  incohérente que npm 10 refusait lors du build de l'image ; il est régénéré et l'image (comme la CI)
  installe désormais avec `npm ci`, qui échoue bruyamment si le fichier dérive à nouveau.
- Les tests de l'endpoint de santé ne dépendent plus de la configuration locale du poste de
  développement.
- Dependabot maintient à jour les dépendances Python, npm, GitHub Actions et les images de base
  Docker (hebdomadaire, groupé, vers `develop`).

**Documentation**
- Suppression des références à des documents de conception qui n'existent plus (`docs/PLAN.md`,
  `docs/PHASE13.md`) et à un `liberty-apps/Dockerfile` qui n'a jamais existé ; le guide d'exploitation
  de nomaflow se trouve dans le dépôt des applications (`docs/NOMAFLOW.md`).

## 7.0.55 — 2026-07-16

**Connecteurs — mode « thick » Oracle (Native Network Encryption)**
- **`[app] oracle_thick`, à l'échelle de l'application.** Un client a activé le chiffrement réseau
  natif d'Oracle (NNE), que le mode « thin » (pur Python) de python-oracledb ne sait pas négocier
  (`DPY-3001`). Lorsque l'option est active, chaque pool Oracle s'appuie sur le client OCI **thick**
  (l'Instant Client embarqué dans l'image) au lieu du pilote asynchrone thin — `ThickAsyncEngine`
  expose la même surface `AsyncEngine` que le framework utilise (execute / stream / begin / dispose)
  au-dessus d'un moteur SQLAlchemy synchrone, en exécutant les appels de chaque connexion sur son
  propre exécuteur mono-thread (les connexions oracledb ne sont pas thread-safe). Les pools Postgres
  ne sont pas concernés.
- L'option vaut **pour tout le processus, pas par pool** : `init_oracle_client()` est global, le choix
  thick / thin est donc tout ou rien. Elle se règle dans Paramètres → Application ; un redémarrage
  l'applique.

## 7.0.54 — 2026-07-14

**Connecteurs — multi-environnement (un connecteur, plusieurs pools)**
- **Un connecteur SQL peut désormais s'exécuter sur plusieurs pools.** Déclarez `pools = ["…"]` en
  plus du `pool` par défaut : les *mêmes* requêtes + écrans s'exécutent sur n'importe lequel — un
  seul connecteur dessert plusieurs instances DB / JDE, sans cloner les connecteurs ni dupliquer les
  écrans. Le pool est choisi à l'exécution :
  - **Interface** — le sélecteur d'application affiche un sélecteur **Pool** lorsque l'application
    courante couvre un connecteur multi-pools ; choisir un pool réexécute ses écrans sur cette
    instance. Le choix voyage via l'en-tête `X-Liberty-Pool` et n'est appliqué qu'aux connecteurs qui
    possèdent ce pool (les écrans d'une application interrogent plusieurs connecteurs — un connecteur
    mono-pool garde son pool par défaut au lieu d'échouer). Le choix est mémorisé par application.
  - **Tâches** — les étapes `sql_copy` / `sql_query` acceptent `source_pool` / `target_pool` pour
    cibler une instance précise sans nouveau connecteur.
- Le `pool` (par défaut) et les `pools` (supplémentaires) du connecteur se choisissent dans une
  **liste déroulante des pools configurés** (Paramètres → Connecteurs → Paramètres…), sans saisie.
- Entièrement rétrocompatible : un connecteur avec seulement `pool` se comporte comme avant.
  L'ensemble autorisé est `[pool] + pools` ; une requête/tâche ne peut atteindre un pool hors de cet
  ensemble. Seuls les *noms* de pool sont exposés à l'interface — jamais l'URL ni les identifiants.

## 7.0.53 — 2026-07-14

**nomaflow**
- **Préréglages planifiables.** Un préréglage de tâche (un jeu de paramètres d'exécution
  enregistré) peut désormais porter son propre cron + fuseau horaire. Le planificateur exécute la
  tâche selon ce planning avec les paramètres / op_kwargs / bascules d'étapes / niveau de log du
  préréglage — une même tâche s'exécute donc sur plusieurs plannings avec des paramètres
  différents, sans clonage. Chaque préréglage a sa propre bascule **activé**, **indépendante du
  `enabled` de la tâche** : un préréglage activé se déclenche même si le planning propre de la
  tâche est désactivé (le `enabled` de la tâche ne gouverne que son propre cron). La vue Planning
  affiche désormais une ligne par planning (le cron de la tâche et celui de chaque préréglage), et
  un déclencheur planifié mais désactivé est signalé.
- **Nommer un clone ; supprimer une tâche.** Le clonage d'une tâche demande désormais le nouvel
  identifiant (celui de l'éditeur est immuable, l'ancien `<id>_copy` automatique était donc
  irrenommable). Une action **Supprimer** retire une tâche de `jobs.toml` (configuration
  uniquement — l'historique des exécutions est conservé).
- **Correction** de la fenêtre « Exécuter avec paramètres » qui débordait horizontalement et
  masquait le bouton « Enregistrer le préréglage ».

**Docker**
- **pgAdmin écoute en IPv4.** Les images récentes `dpage/pgadmin4` utilisent par défaut l'adresse
  d'écoute `[::]` (IPv6) ; sur un hôte où l'IPv6 est désactivée, l'attachement échoue (`Address
  family not supported by protocol`) et pgAdmin ne démarre jamais. La composition full définit
  désormais `PGADMIN_LISTEN_ADDRESS=0.0.0.0`.

## 7.0.52 — 2026-07-05

**Docker**
- **Le client Oracle en mode thick se charge de nouveau sur l'image de base actuelle.** L'Instant
  Client embarqué a besoin de `libaio.so.1`, mais Debian trixie livre libaio sous le nom
  `libaio1t64`, qui fournit `libaio.so.1t64` (renommage lié à la transition time_t) — si bien que
  `init_oracle_client()` échouait avec `DPI-1047: libaio.so.1: cannot open shared object file`
  alors même que le client et libaio étaient présents. L'image installe désormais libaio dans sa
  propre couche (sans `--no-install-recommends`, qui écartait le vrai paquet) et ajoute un lien
  symbolique de compatibilité `libaio.so.1 → libaio.so.1t64` — ABI identique sur les architectures
  64 bits produites — de sorte que la lecture des LOB via database link en mode thick fonctionne
  sans aucune manipulation.
- **Notes de version désormais incluses dans l'image.** Le `.dockerignore` excluait `**/*.md`, ce
  qui retirait `RELEASE.md` / `RELEASE.fr.md` du contexte de build avant leur empaquetage — d'où le
  message « No release notes bundled with this build » dans Paramètres → Notes de version côté
  conteneur (cela fonctionnait depuis les sources en développement). Les deux fichiers de notes sont
  de nouveau ré-inclus.

## 7.0.51 — 2026-07-05

**Connecteurs**
- **Lecture en mode thick des LOB via un database link.** Le mode asynchrone (thin) de
  python-oracledb ne peut pas lire un LOB à travers un database link (échec ORA-22992 / ORA-03149 —
  le LOB revient sous forme de locator distant). `liberty.connectors.thick.fetch_thick(pools, pool,
  sql)` exécute ce type de requête dans un sous-processus éphémère qui active le client thick (OCI)
  *uniquement là*, et renvoie les lignes (LOB matérialisés en bytes) à l'appelant asynchrone — l'app
  principale reste thin/async. L'**Instant Client Oracle est désormais inclus dans l'image**
  (amd64 + arm64), et `PoolRegistry.oracle_connect_params` expose le DSN/identifiants déchiffrés d'un
  pool. À n'utiliser que là où le mode thin ne peut pas (un SELECT de LOB via dblink).

## 7.0.50 — 2026-07-04

**Connecteurs**
- **Liens de base par schéma (`#DBLINK.<NAME>#`)** — un pool peut désormais associer un suffixe de
  database link à chaque schéma (`[pools.X] dblinks = { SY = "@ORCLPROD", … }`), ajouté à la table
  *après* son préfixe `#SCHEMA.<NAME>#` (`…F0092#DBLINK.SY#` → `SY920.F0092@ORCLPROD`). Un jeton non
  mappé ou vide disparaît, si bien que la même requête s'exécute en local quand le schéma n'est pas
  distant ; une valeur non vide doit être une référence `@link` (protection anti-injection).

**nomaflow**
- **Les lignes de log des plugins apparaissent enfin dans le journal d'exécution.** Le `RunLogHandler`
  par exécution n'était attaché qu'à l'arbre `liberty.*` : un espace de noms de plugin enregistré avant
  le démarrage (ex. `nomasx1`) voyait ses lignes sur la sortie standard mais jamais dans l'interface.
  `install()` attache désormais *tous* les espaces de noms enregistrés.
- **Taille du tampon de log configurable** — `[jobs] run_log_max_lines` (5000 par défaut) plafonne le
  tampon circulaire de log par exécution ; augmentez-le pour les jobs émettant des dizaines de milliers
  de lignes (ex. un ETL table par table sur des milliers de tables) afin de ne pas perdre le début.

## 7.0.49 — 2026-06-21

**Documentation**
- Notes de version françaises (`RELEASE.fr.md`) mises à jour jusqu'à la 7.0.48 — entrées rédigées en
  français pour les versions 7.0.39 à 7.0.48 (le fichier était resté figé à la 7.0.27).

## 7.0.48 — 2026-06-21

**Écrans**
- **Option `lock` sur `default_when`** — une valeur par défaut conditionnelle peut désormais être
  pré-remplie sans verrouiller le champ. Avec `lock = false`, la valeur est renseignée lorsque la
  condition devient active mais le champ reste modifiable (par exemple proposer `*ALL` par défaut sur
  une colonne à liste de valeurs tout en laissant l'utilisateur la restreindre) ; sans `lock` ou avec
  `lock = true`, le comportement précédent (valeur imposée, champ en lecture seule) est conservé.

**Paramètres**
- Les notes de version s'affichent à la taille de texte du panneau (le journal des modifications
  n'affiche plus de titres surdimensionnés).

## 7.0.47 — 2026-06-15

**Boîte de dialogue**
- Les **colonnes clés masquées** (ou absentes du formulaire) sont de nouveau correctement prises en
  compte à l'enregistrement : la boîte de dialogue mémorise la valeur d'origine de **toutes** les
  colonnes chargées pour les paramètres `:<COL>_ORIGINAL` de la requête `_put` (et plus seulement les
  champs affichés). Une ligne dont la clé est une colonne masquée met ainsi à jour le bon
  enregistrement.
- La **détection des modifications** ne porte de nouveau que sur les champs modifiables : annuler une
  boîte de dialogue sans rien changer ne déclenche plus la fenêtre « Modifications non enregistrées ».

**Assistants**
- Le générateur CRUD produit des noms de colonnes et de paramètres **en majuscules** (comme
  l'assistant SELECT) : les colonnes JD Edwards ressortent en majuscules.
- Le générateur de liste de valeurs / séquence n'impose plus de choisir une table : on peut **saisir
  directement le SQL** et utiliser quand même « Utiliser cette requête ».

**SQL / erreurs**
- Les erreurs de base de données présentent le **SQL et les paramètres liés dans des blocs lisibles**
  (un paramètre par ligne) dans le détail dépliable.
- Les deux-points contenus dans les chaînes littérales sont échappés avant l'analyse des paramètres
  par SQLAlchemy : un littéral comme `':0'` n'est plus pris pour un paramètre lié.

## 7.0.46 — 2026-06-15

**Authentification**
- **Renouvellement silencieux du jeton** — le jeton d'accès se renouvelle tout seul, en amont de son
  expiration et après toute réponse 401, si bien qu'une session active ne se ferme plus environ
  toutes les heures.

**Erreurs**
- Les erreurs de base de données brutes sont réduites à un **résumé concis assorti d'un détail
  dépliable**, dans la boîte de dialogue d'écran comme dans les bandeaux de la grille (édition en lot,
  écran proxy), au lieu d'afficher l'intégralité du message du pilote.

**Boîte de dialogue**
- Les champs **BOOLEAN / ENUM** désactivés s'affichent en case à cocher / libellé en lecture seule
  (comme dans la grille), et non plus sous la forme du code brut stocké.

## 7.0.45 — 2026-06-14

**Assistant de création d'écran**
- Créez un écran à partir d'une **requête de connecteur existante** (réutilisation en lecture seule),
  et plus seulement d'une table physique ; les presets de catalogue adossés à une requête réutilisent
  celle-ci au lieu d'en générer un double.
- L'étape **Vue grille** (la vue partagée par défaut) est désormais distincte de l'étape facultative
  **Colonnes du dialogue** ; la requête de lecture sélectionne toutes les colonnes, et la vue / le
  dialogue choisissent ce qui est affiché.
- Les écrans créés activent **`auto_load`** et l'espace de travail se rafraîchit après la génération :
  le nouvel écran s'ouvre sans rechargement manuel du navigateur.
- Sélecteurs de colonnes : sélecteur de **menu parent en arborescence**, groupes de tables sources
  **repliables et filtrables**, panneaux défilants pleine hauteur, « tout ajouter » par table et
  indicateurs de chargement.

## 7.0.44 — 2026-06-13

**Piste d'audit**
- **Différentiel de valeurs à la volée** — une ligne se déplie pour afficher ses valeurs AVANT /
  APRÈS champ par champ, reconstituées à la demande à partir de l'instruction DML stockée ; la table
  des valeurs source peut donc être purgée tout en restant reconstituable.
- Traitements de **purge** et de **reconstruction des valeurs** pour gérer l'historique d'audit.
- Améliorations de la vue Synthèse (sous-lignes dépliables natives à la place d'une grille imbriquée).

## 7.0.43 — 2026-06-13

**Grille**
- **Vues de grille enregistrées** — vues partagées nommées et vues par utilisateur (colonnes, tri,
  regroupement, taille de page), accessibles depuis le sélecteur de vues de la grille.
- **Vue Synthèse** — lignes parentes agrégées côté serveur (comptes exacts sur l'ensemble des
  données), avec un chevron qui charge les lignes sous-jacentes à la demande ; regroupement par jour /
  mois / année.

**Filtres et traitements**
- Filtrage à la journée sur les colonnes d'horodatage ; niveau d'exécution **WARNING** pour les
  traitements.

## 7.0.42 — 2026-06-13

**Sécurité**
- Les **colonnes régies par la règle PASSWORD** sont chiffrées au repos (AES-GCM, préfixe `ENC:`), et
  un mot de passe laissé vide lors d'une mise à jour conserve le secret stocké au lieu de l'écraser.

## 7.0.41 — 2026-06-13

**Rapports**
- **Listes déroulantes dans le formulaire d'exécution** — les paramètres de rapport peuvent déclarer
  des `options`, et la fenêtre d'exécution affiche une liste déroulante avec recherche au lieu d'un
  champ de saisie libre. Les choix sont résolus côté serveur (liste statique, connecteurs configurés,
  schémas d'un connecteur, ou requête de connecteur nommée associant valeur et libellé) et peuvent
  fonctionner en cascade — par exemple un sélecteur d'application qui liste les applications par nom
  sur le connecteur choisi.

## 7.0.40 — 2026-06-12

**Documentation**
- README : historique des mises à jour et notes de version, `release/upgrade.sh`, et une procédure de
  reprise en cas d'échec de publication (notamment la limitation du push de tag sur les fichiers de
  workflow par l'application GitHub).

## 7.0.39 — 2026-06-12

**Historique de configuration**
- L'**historique des mises à jour** suit les changements de version du logiciel au démarrage — socle
  **et** applications sous licence, de façon indépendante — présenté sous Paramètres → Historique →
  Mises à jour, avec les notes de version intégrées.
- **Notes de version** livrées dans le paquet (`RELEASE.md` / `.fr`), servies par composant.

## 7.0.27 — 2026-06-12

**Assistant de création d'écran**
- Nouvel **Assistant de création d'écran** (superutilisateur, barre latérale, au-dessus de Monitoring) —
  un assistant guidé qui transforme des tables choisies en écran + dialogue + menu en une passe :
  tables & jointures, colonnes → onglets, revue du dictionnaire et placement du menu.
- **Catalogue de presets** — presets de tables gérés par l'opérateur (`config/presets/`), parcourables
  et recherchables ; en sélectionner un câble automatiquement les jointures. Inclut un catalogue
  JD Edwards Address Book.

**Dictionnaire**
- Table de scan partagée entre l'éditeur de dictionnaire et l'assistant : les entrées existantes sont
  détectées et seules les manquantes sont proposées, avec les listes Format/Règle, la valeur de règle,
  les **paramètres de lookup UDC** et la valeur par défaut.

**Historique de configuration**
- **Historique des mises à niveau** — Paramètres → Historique → Mises à niveau enregistre chaque
  changement de version de l'application.
- **Notes de version** — cette vue.

## 7.0.26 — 2026-06-01

- **Versionnage des bundles** écran + dépendances (Phase 2) : chaque sauvegarde d'écran capture sa
  clôture de dépendances ; la restauration rétablit l'écran *avec* ses requêtes/lookups/entrées de
  dictionnaire.
- Tâche de **purge de rétention** pour les snapshots de configuration ; suppression par version et par
  fichier.
- Mode écran en lecture seule ; déplacer un champ entre onglets de dialogue depuis l'inspecteur.
