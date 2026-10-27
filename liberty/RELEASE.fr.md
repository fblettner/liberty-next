# Liberty Next — Notes de version

## 7.0.59 — non publiée

**Corrections issues de la revue de documentation (27/10/2026)**
- Dialogue : un champ `required`, ou un `required_when` vérifié, bloque désormais l'enregistrement
  quand il est vide (l'astérisque n'était que décoratif).
- Dialogue → Dupliquer : les lignes liées 1:1 (groupes de colonnes) sont insérées sous la nouvelle
  clé. Depuis juin, la copie réinjectait la ligne source derrière le formulaire : le groupe
  exécutait un UPDATE sur la nouvelle clé (0 ligne, en silence) au lieu de son INSERT, et la clé
  source réapparaissait dans le formulaire.
- Configuration → Menus : la liste des cibles d'un élément `dashboard` propose les tableaux de bord
  (par id qualifié, ceux de l'application du menu d'abord) ; elle était vide.
- Paramètres : une valeur par défaut déclarée ou une valeur fournie atteint son bind quelle que
  soit la casse (`status` déclaré, `:STATUS` dans le SQL).
- nomaflow : les préréglages d'une tâche clonée perdent leur planification (ils tournaient deux
  fois : le planificateur lance le cron d'un préréglage même sur une tâche désactivée) ;
  `alerts.on_long_run_minutes` d'une tâche envoie le message de durée excessive à ses
  `alerts.recipients`.
- Notifications : `[notify] retention_days` purge les notifications lues (au démarrage et toutes
  les six heures) ; l'e-mail porte le lien de la règle ; un destinataire de règle sans préfixe qui
  est une adresse est envoyé à cette adresse, un autre mot nu est journalisé au lieu d'être ignoré.
- Assistant écran : les `_post` / `_put` générés écrivent les éléments d'audit JDE (UPMJ, UPMT,
  USER, MUSE, PID, JOBN ; jamais une colonne clé) sous forme de jetons `{{JDEDATE}}` /
  `{{JDETIME}}` / `{{LOGIN}}` / `{{PID}}` / `{{JOBN}}`, la convention des requêtes livrées : une
  colonne d'audit liée gardait la valeur renvoyée par le dialogue à la mise à jour.
- `liberty-admin create-role` sur un rôle existant garde ses permissions sans `--permission` ;
  `set-scope` refuse de s'exécuter sans `--rule` ni `--clear` (il vidait le périmètre).
- Configuration → App : changer `oracle_thick` signale qu'un redémarrage est nécessaire (le mode
  épais se fixe une fois par processus). Le « Enregistrer… » de la vue Graphique applique la
  règle d'id unique des graphiques.
- La confirmation de requête modifiable affichait le texte « table.run ». Les descriptions de
  `ColumnHint.key` et `Screen.actions`, le commentaire SYSDATE du connecteur et la docstring de
  `liberty-admin` disent ce que fait le code. `release/README.md`, `install.sh` et la configuration
  Traefik ne mentionnent plus `--license-key`, un montage des apps en lecture seule, `admin/admin`
  ni `reset-admin-password`.

**Assistant IA : le contexte de la page (C6)**
- L'assistant voit maintenant ce que vous regardez. Tant qu'un écran de données est l'onglet
  actif, le chat joint son contexte à chaque message : l'application, l'écran (connecteur et
  requête de lecture), les filtres actifs, le nombre et les colonnes des lignes chargées, et la
  ligne cliquée ou sur laquelle le menu a été ouvert. « Cet écran », « ces lignes » ou « la ligne
  sélectionnée » se comprennent sans ressaisir de nom ; les requêtes du contexte sont les vraies,
  l'assistant peut donc les exécuter. Une puce au-dessus de la zone de saisie montre ce qui est
  envoyé ; sa croix l'écarte pour cette page. Le contexte voyage dans un second bloc système non
  mis en cache, limité à 4 000 caractères : l'invite et la liste d'outils en cache restent
  intactes (`POST /ai/chat` accepte un `context` optionnel).
- **Questions de départ, choix du modèle, une conversation qui reste.** Configuration → App →
  Assistant IA gagne `starters` : des questions proposées sur une conversation vide, partout
  (`*`), par application ou par `app/écran` (le jeu le plus précis pour la page l'emporte ; un
  clic envoie la question), et `allowed_models` : les modèles qu'un utilisateur peut choisir
  dans le chat en plus du modèle configuré (le sélecteur n'apparaît que s'il y a un choix ; le
  serveur refuse tout autre identifiant). La conversation survit maintenant à la fermeture du
  volet et à un rechargement (les 40 derniers messages, sur cet appareil) ; « nouvelle
  conversation » l'efface. Les listes de l'éditeur App (modèle, effort, niveau de journal,
  connecteurs autorisés) sont le sélecteur partagé avec recherche ; la liste des modèles ajoute
  la famille Claude 5.
- **Les applications livrent leurs propres questions de départ.** Un nouveau fichier par
  application, `ai.toml` (`[ai.<app>] starters`, `[ai.<app>.screens.<id>] starters`), voyage avec
  le lot d'applications comme les autres fichiers par application (fusionné à l'installation,
  rechargé à chaud, versionné) et s'édite sous Configuration → IA. Les questions de l'opérateur
  sous App → Assistant IA l'emportent, clé par clé.
- **Des procédures, et un assistant qui connaît l'installation.** `ai.toml` porte aussi
  `[ai.<app>.playbooks.<id>]` (un titre, une description facultative, la procédure en Markdown),
  édité sous Configuration → IA. Quand un utilisateur demande comment faire quelque chose dans une
  application, l'assistant liste les procédures, charge celle qui convient et en suit les étapes,
  en disant laquelle. Un nouvel outil `describe_instance` résume ce que contient l'installation
  (applications, menus, écrans, tableaux de bord, modèles de l'explorateur, tâches, procédures) :
  « où trouver… » se répond d'après la configuration réelle. Un résultat d'outil au-delà de
  60 000 caractères est coupé, avec une note demandant à l'assistant de restreindre l'appel.
- Le chat replie les appels d'outils d'un tour en une ligne discrète (« 3 appels d'outil », à
  ouvrir pour les voir) ; un appel en échec garde son message visible.
- Listes déroulantes : un code long (un identifiant de modèle) n'est plus tronqué dans la liste.
- Explorateur de données : les champs d'une entité table portent maintenant leurs libellés du
  dictionnaire. Les éléments étaient cherchés sous le connecteur de données (`jdedwards`) alors que
  le dictionnaire les range sous l'application (`nomajde`), comme le font les écrans ; et un champ
  sans libellé affichait son nom en minuscules à côté de son nom en majuscules.

**Installation / mise à jour**
- **Corrigé : la version du lot d'applications apparaissait comme « non installée » sous Docker.**
  Le wheel est déposé dans le montage `/apps`, pas installé comme paquet dans le conteneur en
  cours d'exécution ; `liberty status`, le rapport de mise à jour et le journal du serveur ne le
  voyaient donc jamais. La version vient maintenant du manifeste écrit par l'installateur
  (`.bundled/manifest.json`), les métadonnées du paquet restant la solution de repli sur une
  installation pipx.
- **Nouvelle option `liberty-admin migrate --app X --stamp REV`** : fixe la version Alembic d'une
  application sans rien exécuter, puis applique ce qui est devenu en attente (`--dry-run` le liste).
  C'est la réparation d'une base dont la table de version est en avance sur son schéma réel. (Cause
  corrigée côté applications : `init_schema` marquait la base existante à `head`, déclarant
  appliquées des révisions jamais exécutées ; il ne le fait plus que sur une base neuve.)
- Le rapport de mise à jour n'affiche plus « - » comme étiquette d'image sur une installation non
  épinglée.

**Notifications (première partie) : la boîte de réception et le distributeur**
- Une boîte de réception (`ly2_notification` sur `[notify] pool`, créée au démarrage comme les
  autres tables du framework) et un distributeur à trois canaux : **portail** (une ligne par
  destinataire, poussée aussitôt vers la salle Socket.IO de l'utilisateur), **e-mail** (SMTP
  d'après `[notify]` dans `app.toml` : hôte, port, utilisateur, mot de passe `ENC:`, expéditeur,
  STARTTLS ou TLS implicite) et **action** (une requête ou un point d'accès d'un connecteur, lancé
  avec les valeurs du message, pour un webhook ou une table de journal). Destinataires :
  `user:<nom>`, `role:<nom>` (chaque titulaire actif), `email:<adresse>`. Sujet, corps et lien
  prennent des `{marqueurs}` dans les valeurs de l'événement. Un canal qui échoue est journalisé
  et signalé, sans bloquer les autres. Routes : `GET /api/notifications` (`?unread=1`),
  `GET /api/notifications/unread_count`, `POST /api/notifications/read`, et pour un
  superutilisateur `POST /admin/notify/test` pour essayer les canaux.
- **Des règles** dans `notifications.toml` (`[notifications.<id>]`, rechargées à chaud) : un
  déclencheur, les canaux, les destinataires, un sujet, un corps et un lien avec des
  `{marqueurs}`. Trois déclencheurs. Un **événement d'écran** : une insertion, une mise à jour ou
  une suppression passée par un écran, avec un `when` facultatif sur la ligne écrite, dans la forme
  `field = value` (« STATUS = closed ») ; les marqueurs sont les colonnes de la ligne plus `USER`,
  `APP`, `SCREEN`, `EVENT`. Un **événement de tâche** : `failure`, `succeeded`, `canceled`, ou
  `long_run` avec `long_run_minutes`, pour toutes les tâches ou une liste ; marqueurs `JOB`,
  `RUN_ID`, `STATE`, `ERROR`, `DURATION_MINUTES`, `ROWS`, `EXPECTED_AT`. Les `alerts.recipients` propres à une
  tâche reçoivent un message d'échec même sans règle. Une **action** : une nouvelle étape
  `send_notification` dans les chaînes d'un écran déclenche la règle côté serveur avec les valeurs
  de la ligne ; seule une règle déclarée `kind = "action"` peut l'être ainsi, et la règle garde ses
  canaux et ses destinataires. L'envoi tourne sur sa propre tâche : une écriture ou une exécution
  n'attend jamais un serveur de messagerie.
- **L'interface.** Une cloche dans l'en-tête avec le nombre de non-lues, mise à jour en direct par
  Socket.IO, et une liste déroulante des dernières notifications ; une page Notifications (toutes /
  non lues, tout marquer comme lu ; une ligne ouvre son lien) ; Paramètres → App reçoit une section
  Notifications pour le compte SMTP (hôte, port, expéditeur, utilisateur, mot de passe chiffré au
  repos, STARTTLS ou TLS implicite, rétention) ; Paramètres → Notifications édite les règles : le
  déclencheur (un événement d'écran avec application, écran, événements et condition sur la ligne ;
  un événement de tâche avec tâches, événements et seuil de durée ; ou « déclenchée par une action
  d'écran »), les canaux, les destinataires choisis parmi les utilisateurs et les rôles ou saisis
  comme adresses, les modèles de sujet / corps / lien, la cible du canal action, et un bouton
  « Envoyer un test » qui diffuse la règle avec des valeurs d'exemple.

- **Planifications manquées.** Le planificateur garde ses déclencheurs cron en mémoire : un tir
  qui tombe pendant que le serveur est arrêté n'est jamais connu, et un tir qui arrive pendant que
  l'exécution précédente de la même tâche tourne encore est ignoré ; dans les deux cas l'historique
  gardait un trou silencieux. Un contrôle périodique (`[meta.missed]` dans `jobs.toml` :
  `enabled`, `grace_minutes` 10, `check_interval_minutes` 5, `lookback_hours` 24) liste désormais
  les instants que chaque déclencheur actif aurait dû tirer depuis le dernier contrôle et cherche
  une exécution près de chacun ; un trou est enregistré comme une exécution **`MISSED`** (visible
  dans la liste des exécutions et comme dernière exécution de la tâche ; enregistrée une fois, un
  redémarrage ne la signale jamais deux fois) et remonté comme le nouvel événement de tâche
  **`missed`** pour les règles, avec le marqueur `EXPECTED_AT`. Un tir concurrent abandonné est
  enregistré de la même façon. Une exécution manuelle lancée dans la tolérance compte pour le tir ;
  une tâche qui n'a jamais tourné n'est contrôlée qu'à partir du démarrage du planificateur. Sur la
  page des tâches, un panneau « Planifications manquées » à côté de la rétention modifie la
  politique, montre le dernier contrôle et en lance un (« Contrôler maintenant ») ; routes
  `GET /admin/jobs/missed`, `POST /admin/jobs/missed/check`.

**Explorateur de données (première partie) : le modèle**
- `explorer.toml`, plusieurs modèles par app (`[explorer.<app>.<model>]`, un magasin de données chacun ;
  des tables sans lien entre elles sont des modèles différents) : une entité **pivot** et les entités
  autour, chacune une requête sans paramètre d'un connecteur avec une `cardinality` (une ligne par
  ligne du pivot, ou plusieurs), une `key`, une `default_date`, des `measures` (colonne → agrégat
  par défaut) et des colonnes `hidden` ; les `relations` joignent les entités sur des paires de
  colonnes (`on = [["COMPANY", "COMPANY"]]`, n'importe quelle clé, un ou plusieurs sauts jusqu'au
  pivot). Aucun SQL dans le fichier : les **champs** sont découverts à la demande à partir des
  colonnes résultat de chaque requête, libellés, formatés et typés par le dictionnaire et les
  indications de colonnes de l'écran (`screen`) de l'entité (une règle LOOKUP ou ENUM devient la
  liste de référence du champ), classés `string` / `number` / `date` / `datetime` / `boolean` et
  dimension ou mesure ; les noms de champs sont en majuscules (`sales.QTY` sur Postgres comme sur
  Oracle, la convention du framework) pour qu'une analyse passe d'un dialecte à l'autre. `GET /api/explorer` liste les modèles accessibles (nouvelle permission
  `explorer:<app>:<model>`, `explorer:<app>:*` pour tous), `GET /api/explorer/<app>/<model>/model` renvoie le modèle avec ses champs
  (`?refresh=1` ignore le cache par app, vidé à chaque rechargement) ; une entité en erreur est
  signalée sur le modèle, pas levée. Configuration → Explorateur modifie le modèle : le pivot, les
  entités (l'éditeur d'entité découvre les colonnes de la requête et laisse cocher la clé, les
  mesures avec leur agrégat, les colonnes masquées et la date par défaut) et les relations ;
  `GET/PUT /admin/config/explorer/parsed`, rechargé à chaud, inclus dans la fusion du bundle des
  apps. L'éditeur d'accès propose « Explorateur de données » comme type de règle.
- **Le moteur (mode agrégé).** `POST /api/explorer/<app>/query` reçoit des lignes (dimensions, une
  date avec un `bucket` jour / semaine / mois / trimestre / année), des mesures (`sum` / `avg` /
  `min` / `max` / `count` / `count_distinct` d'un champ numérique, ou `count(*)`), un sac de filtres
  plat (`key`, `key__op`, `key__from` / `key__to`, une liste = IN ; opérateurs selon le type), une
  période sur un champ date, un tri et un Top N, et exécute **un seul** `SELECT dims, aggs … GROUP
  BY`, le même sur Oracle, Postgres et SQLite : les sources de lignes sont l'entité principale (le
  pivot, ou `entity`) plus les entités des dimensions, au plus une d'entre elles côté « plusieurs »
  pour que les lignes ne se multiplient jamais deux fois ; les entités côté « une » sont jointes en
  LEFT JOIN par les relations (autant de sauts que nécessaire) ; une entité côté « plusieurs » hors
  des lignes est atteinte par une sous-requête corrélée pour une mesure et par `EXISTS` pour un
  filtre. Chaque requête d'entité tourne avec la portée de données de l'appelant appliquée dedans
  (`SQLConnector.scoped_select`). Le SQL groupe au jour ; Python replie les dates dans le bucket,
  fusionne les groupes, calcule `avg` à partir de somme + nombre, trie et coupe à la limite
  (`groups`, `scanned`, `truncated` dans la réponse). Une demande que le modèle ne peut pas servir
  est un 422 qui dit pourquoi ; `debug = true` renvoie le SQL et les binds à un superutilisateur.
- **Mode détail, descente, champs calculés.** `POST /api/explorer/<app>/detail` renvoie les
  lignes elles-mêmes : les colonnes demandées (toute entité, mêmes règles de jointure) ou les
  champs visibles de l'entité principale, avec les mêmes filtres et période, un tri, `total` et
  une page (`page` / `page_size`, jusqu'à 5 000) ; `drill` prend les valeurs de dimension d'une
  cellule agrégée et en fait des filtres (un libellé de mois devient la plage de jours du mois,
  un groupe vide les lignes vides) : les lignes derrière un chiffre sont à un appel. Les **champs
  calculés** sont déclarés dans la demande (`calculated = [{name, label, expr}]`) et utilisés
  comme `calc.<name>` dans les lignes, mesures, filtres et colonnes : un petit langage typé
  (`REVENUE / QTY`, `QTY >= 4 ? 'big' : 'small'`, `company.REGION = 'EU'`, `year(SOLD_ON)`,
  `days(ORDERED, SHIPPED)`, `round`, `abs`, `coalesce`, `upper` / `lower` / `length` / `concat`,
  `in (…)`, `is empty`, `and` / `or` / `not`) compilé dans le dialecte du pool au sein de la même
  instruction, jamais du SQL libre ; une division est réelle et protégée par `NULLIF`, un booléen
  s'affiche `Y` / `N`, une date julienne JDE est convertie avant une fonction de date. Un résultat
  numérique est une mesure, tout autre une dimension ; une erreur de type nomme la position.
  `POST /api/explorer/<app>/calc/test` en compile un et prélève huit valeurs : le « Tester »
  de l'éditeur avant « Enregistrer ».
- **La page (première partie).** Une page Explorateur de données (`/explorer`, la boussole dans
  l'en-tête quand l'appelant a un modèle ; un élément de menu `page` peut aussi y mener) avec le
  **volet des champs** du modèle (les entités avec leur badge 1..1 / 1..n, les dimensions puis les
  mesures Σ, une recherche ; un clic ajoute le champ, l'entonnoir l'ajoute en filtre, un
  glisser le dépose sur une étagère ; un double-clic fait d'une entité l'entité analysée), les
  **étagères** — Lignes (une date avec son regroupement), Mesures (un agrégat chacune, « + » pour
  un nombre de lignes, une dimension déposée là compte ses valeurs distinctes) et Filtres (un
  opérateur selon le type, un champ énuméré ou à liste de référence choisit parmi ses valeurs, une
  date reçoit de → à ; les puces se réordonnent en glissant) — une **barre de période** (le champ
  date, les préréglages et le calendrier partagés), un Top N, et le résultat en tableau : les
  mesures en nombres, les dimensions à travers leur règle enum / liste, triable par en-tête.
  L'analyse se relance d'elle-même quand sa définition change. La vue **Détail** liste les lignes
  avec l'étagère Lignes en colonnes, paginée ; un clic sur une ligne agrégée **descend** vers les
  lignes derrière elle (les clés de l'entité, les dimensions, les champs des mesures), avec les
  valeurs de la cellule affichées et un retour. Un superutilisateur peut afficher le SQL.
- **La page (deuxième partie) : croisé, graphiques, KPI, champs calculés, la fiche.** Le résultat
  change de vue sans nouvelle requête. Le **tableau croisé** replie les dimensions des lignes en
  une hiérarchie dépliable (tout déplier / replier) avec une dimension étalée en colonnes (la
  dernière, ou celle choisie), des sous-totaux sur les lignes de groupe pour une mesure additive
  seulement (une somme de moyennes n'a pas de sens, une mesure non additive garde les valeurs des
  feuilles), une part du parent ou du total à côté de chaque valeur, un total général, un export
  Excel et une descente depuis toute cellule feuille. **Barres, courbes et secteurs** passent par
  le canevas de graphique partagé : x est la première dimension, les séries la deuxième (ou les
  mesures), les 10 premières séries ou parts plus « Autres », les dates dans l'ordre
  chronologique et les autres catégories par valeur, une option empilée ; **KPI** montre une carte
  par mesure avec son total. Le tableau et le détail s'exportent aussi vers Excel. Le volet des
  champs gagne les **champs calculés** : « Nouveau champ calculé » ouvre l'éditeur (nom, libellé,
  l'expression avec des sélecteurs insérer-un-champ et insérer-une-fonction, un Tester qui la
  compile côté serveur et montre le type et huit valeurs d'exemple ; Enregistrer demande un test
  réussi), le champ siège ensuite dans une entité `calc` du volet comme mesure ou dimension, avec
  modification et suppression. En vue détail et après une descente, une ligne d'une entité qui a
  un **écran** ouvre le dialogue de cet écran : la requête de lecture de l'écran est exécutée avec
  les valeurs de clé de la ligne et la fiche s'affiche comme partout ailleurs, modifications
  comprises.
- **Analyses enregistrées et tableaux de bord d'analyses.** Un seul objet `AnalysisDefinition`
  pilote l'éditeur, les tableaux de bord et (ensuite) le digest. La page gagne une barre de mode,
  **Analyses** et **Tableaux de bord**, avec un sélecteur des éléments enregistrés (groupés par
  public, « à moi » signalé), Enregistrer, Enregistrer sous et Supprimer. Une analyse ou un tableau
  de bord est rangé dans le magasin des vues enregistrées (`ly2_user_view`, genres `analysis` /
  `explorer_dashboard`, l'app en clé, l'id un slug du nom) : **privé** sous son propriétaire, ou
  **partagé** sous l'utilisateur réservé `*` pour que toute personne ayant la permission
  d'exploration de l'app le voie ; le propriétaire, le public, une version et le marqueur `bundled`
  voyagent dans l'enregistrement. Enregistrer un objet partagé le change pour tout le monde (la
  fenêtre le dit, « Enregistrer sous » fait votre propre copie) ; supprimer un objet partagé revient
  à son propriétaire ou à un superutilisateur. Un **tableau de bord** est une grille de tuiles (une
  analyse enregistrée chacune, 1 à 4 colonnes de large, trois hauteurs), chacune exécutant son
  analyse dans sa propre vue, avec une période qui prime sur les analyses ayant un champ de
  période ; « Modifier la disposition » déplace, redimensionne, retire et ajoute des tuiles ; le
  crayon ouvre l'analyse dans l'éditeur, une descente depuis une tuile aussi, avec les valeurs de la
  cellule en filtres. Un **catalogue livré** accompagne l'app : `[explorer.<app>.analyses.<id>]` /
  `.dashboards.<id>` dans `explorer.toml`, chacun avec une `version`, installés une fois comme
  objets partagés au démarrage et au rechargement, rafraîchis à un changement de version seulement
  tant que personne n'a modifié la copie (un enregistrement utilisateur retire le marqueur), et
  jamais réinstallés une fois qu'un utilisateur les a supprimés (une ligne marqueur des ids
  installés s'en souvient). Routes `GET / PUT /api/explorer/<app>/saved/<analyses|dashboards>`,
  `DELETE …/<id>`.
- **Export et exécution sans écran.** `POST /api/explorer/<app>/export` rend une analyse
  (enregistrée, ou celle de la page) ou un tableau de bord (une section par tuile) en **xlsx**
  (une feuille par section, en-têtes et sous-totaux en gras, la période et les filtres en note) ou
  en **pdf** (par le pipeline des rapports, avec l'habillage de l'installation : un titre, un
  chapitre par section, des tableaux GFM, les lignes de groupe en gras ; demande l'extra
  `reports`). Un croisé s'exporte aplati avec ses sous-totaux, un graphique comme sa table de
  données, un KPI comme une ligne de totaux, un détail comme ses lignes ; 3 000 lignes au plus par
  section. Les boutons sont dans la barre de mode. En dessous, `liberty.explorer.report` porte la
  seule lecture côté serveur d'une définition enregistrée — les préréglages de période résolus
  comme sur la page, une fenêtre forcée facultative (`from` / `to`) — et `build_sections`, que le
  digest C5 appellera sans écran.
- **Un modèle à partir d'un préréglage de l'assistant écran.** Le graphe de jointures est
  déclaré une fois, dans le préréglage (`config/presets/**.toml`, livré avec l'app comme le
  modèle). Dans Configuration → Explorateur, choisir un préréglage dans l'en-tête du modèle
  **remplit le modèle** : chaque table du préréglage devient une entité (`SELECT *` sur la table,
  ou la `query` « colonnes rapides » du préréglage ; l'id est le nom de la table, le connecteur
  celui du préréglage), la table de base le pivot, les jointures des relations sur des **éléments
  de données** (`F0101.AN8 = F0111.AN8`) résolues sur les colonnes découvertes : la colonne de ce
  nom, celle dont c'est l'élément de dictionnaire, ou sur un connecteur à `dd_prefix` celle dont
  le nom moins le préfixe est cet élément (`ABAN8`, `WWAN8`). Les entités déjà déclarées sont
  gardées et complétées (la clé, les mesures, la date par défaut, une cardinalité restent les
  vôtres) ; le résultat est ce qui s'enregistre et l'exécution ne lit que cela. Une entité peut
  désormais être une `table` (+ `schema`) au lieu d'une requête, et l'éditeur d'entité découvre
  aussi les colonnes d'une table. Routes `POST /admin/explorer/from-preset`,
  `POST /admin/explorer/describe-entity` ; le catalogue de l'assistant écran lit les mêmes fichiers
  par le nouveau chargeur `liberty.presets`.

**nomaflow : le digest — étapes `report` et `notify`**
- Deux nouveaux types d'étape font d'un digest planifié et envoyé par e-mail une simple tâche.
  **`report`** rend une analyse enregistrée de l'Explorateur (`report = "analysis:<id>"`) ou un
  tableau de bord (`dashboard:<id>`, une section par tuile) d'une `app` / `model`, ou un rapport
  de plugin (`report:<scope>:<id>`, avec `params`), au `format` xlsx ou pdf, dans l'espace de
  travail du run (le détail du run propose le fichier). La période est le préréglage `period` de
  l'étape (`lastMonth`, `thisQuarter`…) ou `window_from` / `window_to`, sinon celle de l'analyse.
  **Visibilité** : `as_user = "cfo"` rend sous la portée de données de cet utilisateur (les
  règles de ses rôles avec ses attributs — un digest « filiale » ne contient que cette société) ;
  `as_role` sous les règles littérales d'un rôle ; ni l'un ni l'autre, sans restriction.
  **`notify`** envoie le message par le distributeur de notifications — `recipients` (`user:` /
  `role:` / `email:`), `subject` et `body` avec `{JOB}` `{RUN_ID}` `{FILES}` `{DATE}`, `channels`
  — avec les fichiers du run **en pièces jointes** (`attach = ["*"]`, ou des noms). Les deux se
  modifient dans l'éditeur de tâche (le modèle, les analyses et tableaux de bord partagés, les
  rapports, les rôles se choisissent dans des listes) ; la planification est le cron ou les
  préréglages de la tâche, comme d'habitude. Les exécuteurs lisent l'état vivant de l'application,
  un rechargement est donc vu.

**Écrans : `editable_when`, une règle de lecture seule par ligne**
- Un écran peut dire quand une ligne existante peut être modifiée : `editable_when = [{ field =
  "PERIOD_STATUS", value = ["open", "validated"] }]` garde en lecture seule les lignes d'une période
  close. Le mode édition de la grille laisse ces lignes telles quelles et affiche un cadenas à la
  place de la croix de suppression, la fenêtre de saisie les ouvre en consultation, et le serveur
  refuse une mise à jour ou une suppression dont la ligne d'origine ne satisfait pas la règle (409),
  y compris une requête qui omet le champ de la règle. Les nouvelles lignes ne sont pas concernées.
  Les règles se combinent par ET, les valeurs se comparent sans leurs espaces, comme `disabled_when`.
  Se règle dans l'onglet Général de l'éditeur d'écran.

**Tableaux de bord : une période**
- Un tableau de bord peut porter une `period` : un sélecteur de plage de dates dans sa barre, avec
  les raccourcis habituels (aujourd'hui, hier, 7 / 30 derniers jours, ce mois-ci / mois dernier,
  ce trimestre, cette année / année dernière, 12 derniers mois, toutes les dates), un calendrier
  sur deux mois pour une plage libre et, quand le tableau de bord liste plusieurs dates de
  référence (« Date document », « Payé le »), le choix de la référence. Le tableau de bord s'ouvre
  sur `period.default`. Chaque requête de widget reçoit la plage dans les liaisons `:PERIOD_FROM`
  / `:PERIOD_TO` (liées comme des dates), plus `:PREV_FROM` / `:PREV_TO` pour la période précédente (une année à date se
  compare à l'année précédente à date, un mois au mois précédent à date, un trimestre entier au
  trimestre précédent, le reste à la même durée juste avant) et `:PERIOD_BASIS` ; une requête qui
  ne les mentionne pas reste telle quelle (« Toutes les dates » lie 0001-01-01 à 9999-12-31). Quand la période nomme la colonne de date (`column`, ou
  `dd` retrouvé par les écrans), un widget dont l'écran la porte est filtré côté serveur sans
  toucher à son SQL : l'enveloppe des filtres, avec maintenant des bornes `<COL>_from` /
  `<COL>_to` sur toute colonne connue. L'éditeur de tableau de bord a un bloc Période. Le sélecteur
  de plage et le panneau flottant sont des composants partagés, pour les listes et l'explorateur
  à venir.
- Des valeurs par défaut au niveau de l'application dans Paramètres → App : `default_period` (le
  raccourci sur lequel s'ouvre un tableau de bord qui n'en nomme pas ; un choix dure
  tant que l'onglet du tableau de bord reste ouvert),
  `default_page_size` (la taille de page initiale de la grille ; le plafond de lignes d'un SELECT
  reste sur le pool, le connecteur et l'écran) et `dashboard_cache_seconds`. Un tableau de bord
  rouvert réutilise le résultat de chaque widget pendant cette durée ; le bouton Actualiser de la
  barre passe outre et indique l'heure des données.
- Un widget KPI avec `compare_previous = true` lance aussi sa requête sur la période précédente et
  affiche l'évolution (en pourcentage, ou en points pour un KPI en pourcentage, en vert ou en
  rouge) et la valeur précédente sous le nombre. L'éditeur de widget a la case à cocher.

**nomaflow : étape `file_load`, un classeur ou un CSV dans une table, rejouable par partition**
- Ce que l'import Excel de la grille n'est pas : un chargement qui tourne sans personne au clavier
  (une planification, une chaîne d'étapes), sur les volumes d'une table de faits, rejouable pour une
  période sans toucher aux autres, et qui laisse une trace. L'étape prend un `path` (avec
  `${params.x}` depuis les paramètres d'exécution et `${run.id}`), une `sheet`, la `header_row`, le
  `delimiter` CSV (détecté s'il est vide), un connecteur / une table `target`, `columns` qui relie
  les colonnes cibles aux en-têtes du fichier ou à des `#positions` (les autres se retrouvent par
  leur nom), `constants` pour les colonnes que le fichier ne porte pas (`period =
  "${params.period}"`, `batch_id = "${run.id}"`), `load_mode = "append" | "replace_partition"` avec
  `partition_by`, et `on_error = "fail" | "skip"`.
- Le fichier est lu en flux, jamais chargé entier ; les cellules sont converties au type de la
  colonne cible (entiers, décimaux avec l'un ou l'autre séparateur, dates dans les formes usuelles,
  booléens, texte ; un code que le classeur a transformé en nombre redevient du texte).
  `replace_partition` supprime les partitions présentes dans le chargement (d'après les constantes,
  sinon les valeurs distinctes trouvées dans le fichier) et insère les lignes dans **une seule
  transaction** : un chargement en échec laisse les données précédentes en place, un mois corrigé
  remplace ce mois seulement. Le journal d'exécution et le résultat de l'étape donnent les lignes
  lues, insérées, rejetées, les partitions remplacées et les lignes supprimées, avec les premiers
  rejets cités. L'éditeur d'étapes a son formulaire.
- Trois façons de nommer le fichier. Le `path` de l'étape ; un fichier choisi dans la fenêtre
  « Lancer avec des paramètres », envoyé dans l'espace de travail de la tâche et remis à l'étape
  pour ce lancement (il l'emporte sur le chemin) ; ou un `watch_dir` + `watch_pattern`, où chaque
  fichier correspondant est chargé dans l'ordre des noms, chacun dans sa transaction, puis déplacé
  dans `done/` ou `failed/` sous un nom horodaté : le cas planifié « déposer le fichier sur le
  partage ». Les lignes rejetées vont dans un CSV (fichier, ligne, motif) que le détail de
  l'exécution propose au téléchargement. `[jobs] files_dir` nomme l'espace de travail (par défaut
  `<dossier de config>/.nomaflow/files`), `max_upload_mb` plafonne un envoi.
- Deux garde-fous. `refuse_query`, une requête nommée lancée avant de toucher à quoi que ce soit,
  avec les paramètres d'exécution et les constantes comme liaisons : une ligne en retour refuse le
  chargement et sa première colonne donne le motif (« period 2026-02 is closed »).
  `control_column` + `control_total` : la colonne est sommée sur les lignes chargées et comparée,
  à `control_tolerance` près, à une valeur, un paramètre ou une cellule du fichier
  (`@Totals!B2`) ; un écart annule le chargement de ce fichier et cite les deux nombres.

**Périmètre de données par ligne (première partie)**
- Un rôle peut maintenant restreindre **les lignes** que ses titulaires voient, en plus des
  requêtes qu'ils peuvent exécuter : `[roles.<r>] scope = [{dd = "CO", values = ["00001"]}, …]`
  dans `auth.toml` (le backend base de données garde la même liste dans `ly2_roles.scope`). Une
  règle fixe un élément du dictionnaire (`dd`) ou une colonne de résultat (`column`) à un ensemble
  de valeurs, littérales ou issues d'un attribut de l'utilisateur (`source = "#USER.company#"` ; les
  utilisateurs portent des `attributes` libres). Les rôles se combinent par OU, les colonnes d'un
  même rôle par ET ; un rôle sans périmètre donne accès à tout ; les superutilisateurs ne sont pas
  concernés. `mode` fixe le sort d'une requête sans l'élément : `ignore` (aucune restriction sur cet
  axe) ou `deny` (aucune ligne). `exclude = true` inverse la liste (`SY NOT IN ('00')`, les valeurs
  nulles restent visibles). Une règle peut nommer son `connector` ; ailleurs elle ne dit rien.
  Le périmètre est calculé à la connexion et voyage dans le jeton, sans accès à la base à chaque
  requête.
- La règle `dd` est celle qui protège un élément dans toutes les tables. Une colonne de résultat
  porte l'élément quand l'indication de l'écran le dit, quand elle porte son nom, ou, sur un
  connecteur avec le nouveau `dd_prefix` (JDE : `dd_prefix = 2`, donc `ABCO`, `MCCO`, `SDCO`
  portent tous `CO`), quand son nom sans le préfixe de table est l'élément. Toutes les colonnes
  concernées d'une requête sont restreintes (une jointure F0101 / F0006 filtre `ABCO` et `MCCO`),
  sans indication d'écran colonne par colonne. Les colonnes texte suivent les règles de dialecte
  des filtres : avec `trim_strings` sur le pool (CHAR / NCHAR JDE, complétés par des espaces), la
  colonne est comparée sans ses espaces et les valeurs nettoyées.
- Appliqué en un seul endroit, le connecteur SQL : chaque SELECT (grille, flux, synthèse, export,
  tableaux de bord, requêtes de l'assistant, rapports) est enveloppé dans `SELECT * FROM (<sql>)
  lib_rls WHERE …` avec de vrais paramètres, et une mise à jour ou une suppression d'une ligne hors
  périmètre est refusée (403). `/auth/me` renvoie les attributs et le périmètre calculé.
- **Les rôles se modifient dans un panneau, sur l'arbre des menus.** Paramètres → Accès → Rôles
  affiche le rôle à côté de la liste, à la place d'une fenêtre remplie de chaînes de permissions. Le
  rôle reprend l'arbre de la barre latérale, un bloc par application, avec hériter / autoriser /
  refuser sur chaque nœud : un dossier se propage à ses enfants, une feuille montre ce qu'elle
  exécute, et un œil indique si la barre latérale l'affichera pour un titulaire du rôle, jokers
  compris. « Tout sauf le dossier Sécurité », c'est la base « accès complet » plus un refus sur le
  dossier ; « ce dossier seulement », c'est la base vide plus une autorisation. Les règles hors
  menus (un connecteur entier, une requête, un point d'accès, un tableau de bord, l'assistant, un
  motif expert) se trouvent sous l'arbre. Les chaînes écrites sont celles de l'éditeur précédent :
  les rôles existants s'ouvrent tels quels et rien ne change côté serveur.
- **Le périmètre de données dans l'éditeur de rôle, les attributs sur l'utilisateur.** Le panneau du
  rôle gagne un onglet Périmètre de données : une ligne par règle (« Société (CO) in 00001, 00050 »,
  « SY not in 00 », « CO = user.company »), avec un formulaire qui choisit l'élément dans le
  dictionnaire, ou un simple nom de colonne pour une colonne sans élément (proposé d'après les
  écrans du connecteur), les valeurs dans l'énumération ou la recherche de
  l'élément quand il en a une, l'attribut utilisateur à lire à la place, l'exclusion, ce que renvoie
  une requête sans l'élément, et un connecteur facultatif. Le formulaire utilisateur reçoit une
  liste « Attributs de périmètre » (`company = 00001, 00050`, une virgule fait une liste ; les clés
  sont proposées d'après les règles des rôles), et le profil affiche les attributs de l'utilisateur
  et son périmètre calculé, en clair.
- Mise en place sans l'interface : `liberty-admin create-role <r> --scope '{dd =
  "CO", source = "#USER.company#", mode = "deny"}'`, `set-scope <r> --rule … | --clear`,
  `list-roles`, `create-user --attribute company=00001,00050`, `set-attributes <u> CLÉ=VALEUR…
  [--replace]` (`CLÉ=` supprime) ; la même chose par `PUT /admin/access/roles/{name}` (`scope`) et
  les `attributes` des utilisateurs. Avec OIDC, `[oidc] attribute_claims = { company = "company",
  pole = "department" }` remplit les attributs depuis le jeton d'identité à chaque connexion (un
  claim qui disparaît retire son attribut ; les attributs posés localement sont conservés).

## 7.0.58 — 2026-10-27

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
- **`liberty upgrade` produit un rapport et `liberty rollback` annule une mise à jour.** Chaque
  exécution enregistre ce qui s'est passé (versions avant et après, étiquette d'image, fichiers
  compose, dossier de sauvegarde, constats du diagnostic, résultat de chaque étape, migrations de
  schéma appliquées) en JSON et en Markdown dans le volume de configuration (`.versions/upgrades/`)
  et l'ajoute à Paramètres → Historique → Mises à jour, à côté des changements de version
  (`liberty-admin record-upgrade`). Avant le téléchargement de la nouvelle image, l'image en cours
  d'exécution est conservée sous une étiquette locale `rollback-<horodatage>` ; `liberty rollback`
  restaure la sauvegarde prise avant la mise à jour et relance cette image.
- Le contrôle de santé du conteneur interroge maintenant `/ready` (la base du framework répond) au
  lieu de `/info` (le processus est démarré) : une pile dont la base est injoignable apparaît
  `unhealthy`.
- Au démarrage, le conteneur signale dans son journal les migrations de schéma en attente ;
  `LIBERTY_AUTO_MIGRATE=1` les applique au démarrage.
- **Corrigé : une version qui ajoute une colonne à l'une des tables du framework ne casse plus les
  installations mises à jour.** L'historique des exécutions nomaflow, les paquets de changements, les
  vues enregistrées et les tables `ly2_*` optionnelles étaient créés avec `create_all`, qui ne modifie
  jamais une table existante (les paquets de changements portaient une liste d'`ALTER TABLE` écrite
  à la main pour cette raison). Au démarrage, chaque module compare maintenant ses modèles aux tables
  réelles et ajoute les colonnes manquantes, avec la valeur par défaut du modèle pour les lignes
  existantes ; les ajouts sont consignés sous `framework-schema` dans Paramètres → Historique → Mises
  à jour, à côté des migrations des applications.
- **Corrigé : la mise à jour des applications sous licence ne perd plus les modifications de
  l'exploitant et n'oublie plus les ajouts du produit.** `liberty-apps install` copiait des fichiers
  entiers : sans `--force-config`, un fichier existant était conservé tel quel (un nouvel écran, une
  nouvelle entrée de menu ou de dictionnaire n'arrivait jamais) ; avec l'option, chaque fichier était
  remplacé (vos modifications dans les écrans, menus, dictionnaire, tableaux de bord, graphiques et
  actions étaient perdues ; l'indicateur `override` était ignoré, seul `connectors.toml` gardait ses
  secrets). L'installateur fusionne maintenant **entité par entité**, avec les règles que Paramètres →
  Package & Deploy appliquait déjà : les ajouts du produit sont ajoutés ; les entités marquées
  `override = true`, celles que vous avez créées et celles que vous avez modifiées sont conservées ;
  les entités du produit jamais touchées sont rafraîchies ; ce que le produit a retiré n'est supprimé
  que si vous n'y aviez pas touché. `[pools.*]` et chaque secret `ENC:` viennent toujours de votre
  fichier, `theme.toml` est le vôtre. La livraison est enregistrée dans `<config>/.bundled/` pour que
  la mise à jour suivante distingue « intact » de « modifié ». `--force-config` signifie désormais
  « rafraîchir aussi les entités du produit que j'ai modifiées sans les marquer » ; `--dry-run` affiche
  le plan, et `liberty apps` / `liberty upgrade --apps` le montrent avant d'écrire. Les entités
  conservées parce que modifiées localement sont listées pour vérification. (`liberty.config_merge`,
  partagé avec Package & Deploy.)
- **Durcissement des dispositions livrées.** Le conteneur exécute l'application sous l'utilisateur
  `liberty` (uid 1000) et non plus root : il démarre en root uniquement pour corriger le
  propriétaire du volume de configuration et du montage des applications d'une installation
  antérieure, puis abandonne ses privilèges (`LIBERTY_RUN_AS_ROOT=1` pour l'ancien comportement).
  Postgres n'est publié que sur `127.0.0.1` (`POSTGRES_BIND=0.0.0.0` l'ouvre). Le tableau de bord
  Traefik reçoit un mot de passe aléatoire par installation (`liberty dashboard-password` en
  génère un nouveau ; `doctor` signale l'ancien mot de passe par défaut). Les surcouches TLS
  redirigent HTTP vers HTTPS et envoient les en-têtes de sécurité. Chaque variable `LIBERTY_*` /
  `POSTGRES_*` / `PGADMIN_*` se lit aussi depuis `<NOM>_FILE` (secrets Docker / Swarm /
  Kubernetes).
- **Chaîne d'approvisionnement.** Les dépendances Python sont figées dans `requirements.txt` (un
  verrou pip-compile partagé par l'image, l'intégration continue et l'environnement de
  développement ; il correspond à ce que l'image 7.0.57 exécutait déjà) ; les images de base et
  les services compose sont épinglés à une version précise et mis à jour par Dependabot ; la
  publication attache un SBOM et une provenance à l'image et exécute Trivy (échec sur une faille
  CRITICAL / HIGH corrigeable). Le client Oracle Instant Client est téléchargé chez Oracle à la
  construction de l'image et vérifié par SHA-256, au lieu de 146 Mo d'archives dans git (une
  copie locale reste possible pour une construction hors ligne).
- **Identifiants de requête et journaux JSON.** Chaque réponse porte `X-Request-ID` (celui de
  l'appelant ou un nouveau) et chaque ligne de journal écrite pendant son traitement porte le
  même identifiant ; `[app] log_format = "json"` émet un objet JSON par ligne.
- **Nouvelle commande `liberty-ops` pour les installations Python** (pipx ou venv, avec ou sans
  systemd), pendant de `release/liberty` : `status`, `doctor`, `start` / `stop` / `restart` / `logs`,
  `backup` / `restore` (dossier de configuration, dossier des applications, fichier SQLite,
  `pg_dump`), `migrate`, `upgrade [--version X] [--apps WHEEL]` (diagnostic → sauvegarde
  obligatoire → `pipx upgrade` ou version épinglée → wheel des applications → migrations →
  redémarrage → rapport → état) et `rollback` (version précédente et sauvegarde prise avant la mise
  à jour). Le rapport est le même que celui du chemin Docker, dans Paramètres → Historique → Mises à
  jour.

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
