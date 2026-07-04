# Liberty Next — Notes de version

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
