# Liberty Next — Notes de version

## 7.0.84 — non publiée

- **Liberty Next a une licence : la Business Source License 1.1** (`LICENSE`, `NOTICE`). Code source
  disponible, pas Open Source : libre de lire, copier, modifier et forker, et d'utiliser en production pour
  votre organisation et vos propres applications — les applications, la configuration et les plugins que vous
  construisez dessus sont à vous, y compris pour les vendre. Vendre ou revendre Liberty Next lui-même (ou une
  version modifiée), ou l'offrir en service hébergé payant, demande l'accord écrit de Nomana-IT. Chaque version
  devient disponible sous GNU AGPL v3 ou ultérieure quatre ans après sa sortie. Elle s'applique à partir de
  cette version ; les versions antérieures ont été publiées sans licence. Les applications de Nomana-IT
  (nomasx1, nomajde…) sont des produits distincts, sous leurs propres conditions. Les contributions passent
  par des forks et des pull requests, sous un accord de contribution (`CONTRIBUTING.md`, `CLA.md`).
- **Sécurité — connecteurs API : un paramètre ne peut plus détourner un point d'accès.** Les
  `{{paramètres}}` d'un point d'accès étaient remplacés comme du texte brut, dans le chemin et dans le corps.
  Un appelant ayant la permission sur un point d'accès pouvait donc atteindre un autre chemin du même
  connecteur (une valeur comme `../autre/service`), ou ajouter des champs à un corps JSON (une valeur
  contenant un guillemet) — la permission `api:<connecteur>:<point d'accès>` valait en pratique pour tout le
  connecteur. Désormais :
  - dans un **chemin**, une valeur peut ajouter des segments (`report/execute`) mais un segment `.` / `..`
    est refusé — écrit tel quel, encodé en pourcent ou avec une barre oblique inversée — `?` et `#` sont
    encodés, et une valeur ne peut pas donner l'adresse de l'appel ; l'appel est refusé avant tout envoi
    (pas même une demande de jeton) ;
  - dans un **corps JSON**, une valeur placée dans une chaîne (`"name": "{{NAME}}"`) est insérée comme
    contenu de chaîne et ne peut pas fermer cette chaîne. Une valeur déjà échappée par l'appelant est
    conservée telle quelle ; une valeur qui ne l'était pas (un chemin Windows, un saut de ligne, un
    guillemet) est maintenant échappée au lieu de produire un corps invalide. Un paramètre écrit hors
    guillemets (`{{BODY}}`, un nombre) reste inséré tel quel : c'est le choix de l'auteur ;
  - dans un **corps encodé en formulaire**, la valeur d'une paire `nom=valeur` est encodée et ne peut pas
    ajouter une paire (`&admin=1`) ; une valeur déjà encodée est conservée ; un paramètre seul (`{{BODY}}`)
    reste inséré tel quel ;
  - le **fichier d'une partie multipart** (`file=@…`) : quand les valeurs viennent d'un utilisateur — la
    route HTTP, une action d'écran, l'assistant, une règle de notification, une modification rejouée — le
    point d'accès doit nommer le dossier (`@/data/out/{{NAME}}`) et la valeur reste dans ce dossier ;
    `@{{FILE}}` est alors refusé, car la permission sur le point d'accès permettrait d'envoyer n'importe quel
    fichier du serveur. Une tâche ou un plugin donne toujours le chemin qu'il veut. Une partie fichier
    illisible donne maintenant un appel en échec avec sa raison, et non une erreur 500 ;
  - une valeur n'est plus relue pour y remplacer des paramètres (une valeur contenant `{{token}}` reste ce
    texte).
  Les points d'accès existants continuent de fonctionner : seuls changent les appels qui sortaient de leur
  chemin, produisaient un corps invalide, ou laissaient un utilisateur choisir le fichier envoyé.

- **Connecteurs API : un paramètre dit ce qu'il attend.** Un paramètre de point d'accès (ou de requête) avait
  un nom, un libellé et une valeur par défaut. Il a maintenant aussi un texte d'**aide** et un **exemple**,
  saisis sur une seconde ligne sous chaque paramètre dans l'éditeur du point d'accès, affichés sous les champs
  dans l'exécuteur HTTP et dans le volet de test de l'éditeur, et transmis à l'assistant IA avec le point
  d'accès. Un paramètre JSON n'est plus à deviner.
  Un paramètre qui n'a que quelques valeurs possibles les liste (**valeurs autorisées**, un troisième champ
  sur cette ligne) : c'est alors une liste déroulante là où il est renseigné, et un point d'accès le contrôle
  — une autre valeur est refusée avant tout envoi, et la casse est corrigée (`VIEW` → `view`).
  Un paramètre déclaré que l'appelant ne renseigne pas, ou envoie vide, prend maintenant sa valeur par défaut
  (vide s'il n'en a pas) ; il restait auparavant dans la requête sous la forme du texte `{{NOM}}`, ou
  remplaçait la valeur par défaut par rien.
  Les points d'accès du connecteur sont listés par ordre alphabétique, dans l'onglet Points d'accès — avec
  une zone de recherche (nom, libellé, chemin, méthode, description) dès qu'il y en a plus de cinq — et dans
  l'onglet Test.

- **Connecteurs API : créer un point d'accès à partir de ce que le serveur décrit.** Un point d'accès générique
  (« lancer n'importe quel état », avec du JSON pour tout ce qui dépend de l'état) est difficile à renseigner.
  Un connecteur peut maintenant déclarer des **générateurs** (`[[connectors.<nom>.generators]]` : un id, un
  libellé, une fonction de plugin) : l'onglet Points d'accès propose alors un bouton **Créer à partir de…** —
  on cherche l'objet, on répond à quelques questions (quelles colonnes, quelles options), et le point d'accès
  est écrit avec une variable nommée par entrée, son libellé, son aide et sa valeur par défaut. Il est ajouté
  non enregistré et ouvert, pour relecture et renommage avant d'enregistrer ; il a sa propre permission
  `api:<connecteur>:<point d'accès>`, ce qui permet d'autoriser à un rôle un état ou une orchestration, et non
  tous.
  Le générateur appartient à l'application (une fonction `j_` sous plugins/, appelée en trois étapes :
  `search`, `options`, `build`) ; le framework l'exécute sur le connecteur enregistré
  (`POST /admin/config/api/generate`, super-utilisateur) et valide le point d'accès construit. Les générateurs
  du connecteur se modifient depuis l'onglet Points d'accès.
  Un point d'accès généré retient ce à partir de quoi il a été créé : **Actualiser depuis le serveur**, sur le
  point d'accès, le reconstruit et montre ce qui change avant de l'appliquer — l'adresse, le corps, la liste
  des paramètres suivent le serveur ; votre nom, votre libellé, votre description et ce que vous avez écrit
  sur un paramètre conservé sont gardés. **Vérifier les générés** interroge le serveur pour chaque point
  d'accès généré et liste ceux qui ne correspondent plus. Les contrôles d'intégrité signalent un point d'accès
  généré dont le générateur n'est plus déclaré sur le connecteur.

- **Assistant IA : un point d'accès API qui modifie des données ne s'appelle pas comme un point d'accès de
  lecture.** Avec l'outil API activé (`[ai] api_tool`), `api_call` appelait tout point d'accès autorisé à
  l'utilisateur — un état soumis, une orchestration lancée — sans carte, sans `ai:write`, et en MCP sans
  `[ai] mcp_changes`. Un point d'accès indique maintenant qu'il **modifie des données** (une case à cocher
  dans l'éditeur du point d'accès, `writable = true` ; une étiquette « modifie des données » dans la liste) :
  - `api_call` le refuse et le liste avec `changes_data` ;
  - un nouvel outil, **`api_run`**, l'appelle — proposé quand `[ai] api_tool` et `[ai] write_tools` sont
    tous deux activés, à un utilisateur ayant `ai:write` et la permission du point d'accès
    `api:<connecteur>:<point d'accès>`. Dans le chat Liberty, il contrôle tout (le point d'accès, la
    permission, chaque valeur) et propose une **carte** ; rien n'est envoyé tant que l'utilisateur ne l'applique
    pas (`POST /ai/api-run`). En MCP il effectue l'appel, et demande `[ai] mcp_changes` ;
  - l'appel figure dans le journal d'usage comme les autres modifications faites par l'assistant.
  Un point d'accès non marqué est traité comme une lecture, comme avant : **marquez les points d'accès qui
  écrivent** sur vos connecteurs API.

- **Connecteurs API : un serveur qui répond 200 avec ses erreurs à l'intérieur.** Certains serveurs signalent un
  refus dans le corps d'une réponse réussie (un formulaire JD Edwards qui refuse un enregistrement répond 200
  avec une liste `errors`). L'appel était alors un succès pour Liberty : le volet de test l'indiquait, une
  action passait à l'étape suivante. Un point d'accès nomme maintenant ses **champs d'erreur** (chemins à
  points, dans l'éditeur du point d'accès) : quand l'un d'eux contient quelque chose, l'appel est un échec et
  son erreur est ce contenu (« Alpha Name Cannot Be Blank »). La réponse complète reste retournée. Les
  **champs d'avertissement** fonctionnent de la même façon sans faire échouer l'appel : leur contenu est
  rapporté avec la réponse (`warnings`), dans l'exécuteur HTTP, le volet de test et à l'assistant. Un chemin
  peut se terminer par un filtre sur les éléments d'une liste, `result.errors[errorLevel=1]`, pour un serveur
  qui place ses erreurs et ses avertissements dans une même liste.

- **Un écran peut lire un point d'accès API.** Un écran lisait une requête nommée d'un connecteur SQL, rien
  d'autre ; la réponse d'un point d'accès était du JSON dans l'exécuteur HTTP. Un point d'accès dont la réponse
  contient une liste d'enregistrements indique maintenant où elle se trouve — **Lignes** dans l'éditeur du
  point d'accès (`rows_field`), avec la correspondance entre les clés des enregistrements et les noms de
  colonnes (`row_map`), l'emplacement des titres de colonnes (`labels_field`) et de l'indicateur « il y en a
  d'autres » du serveur (`more_field`) — et un écran le désigne comme sa lecture : `connector` = le connecteur
  API, `read_query` = le point d'accès. La grille ne change pas : la même adresse sert les deux, avec les
  mêmes colonnes, les libellés de l'écran, puis du dictionnaire, puis les titres du serveur.
  Ce qui diffère d'une requête, par conception : les **filtres de l'écran sont les paramètres du point
  d'accès** (un filtre sur une colonne pour laquelle le point d'accès n'a pas de paramètre n'est pas proposé ;
  les opérateurs du panneau de filtres ne sont pas traduits) ; la **limite de lignes est celle du point
  d'accès**, plafonnée à nouveau par l'écran, sans pagination — l'écran signale un résultat tronqué quand le
  serveur indique qu'il y en a d'autres ; un point d'accès peut désigner le paramètre qui
  porte sa limite (**Paramètre du nombre de lignes**, `limit_param`) : l'écran le remplit alors avec son
  propre « Max lignes » et ne l'affiche plus comme une saisie ; les autres paramètres restent au-dessus de la
  grille (leur exemple en indication, leur aide en infobulle) sauf si une colonne filtre du même nom les place
  dans le panneau de filtres ; une valeur choisie dans une liste est envoyée sans ses blancs de remplissage ; le tri et le regroupement se font dans le navigateur sur ce qui a été
  retourné ; le **périmètre de données** de l'appelant s'applique aux lignes retournées, chaque règle avec son
  propre mode comme pour une requête. La permission est celle du point d'accès
  (`api:<connecteur>:<point d'accès>`), dans la route de la grille, le catalogue des écrans et les menus.
  **Enregistrer par des points d'accès.** Les requêtes de mise à jour, d'insertion et de suppression de
  l'écran peuvent désigner des points d'accès marqués « Modifie des données » : la grille et la fiche
  enregistrent comme toujours, et les valeurs de la ligne vont aux variables du point d'accès par leur nom (la
  valeur que la ligne avait au chargement sous `<COLONNE>_ORIGINAL`, comme pour une requête). Il faut la
  permission du point d'accès ; le `editable_when` de l'écran est appliqué ; ce que le serveur refuse (ses
  champs d'erreur) est l'erreur de l'enregistrement, avec les mots du serveur ; ses avertissements reviennent
  avec la réponse ; une valeur que le point d'accès extrait de sa réponse (`response_map`) revient dans la
  fiche, comme un numéro attribué par une séquence ; les règles de notification se déclenchent. Contrôlé par
  le rapport d'intégrité. L'assistant lit les lignes d'un tel écran avec `sql_query` (les paramètres du point
  d'accès sont ses seuls filtres ; les colonnes sensibles sont écartées comme pour une requête), et ses outils
  d'écriture passent par le même enregistrement.
  Pas sur un écran API : l'import de lignes, l'export de classeur configuré (l'export de la grille
  fonctionne), le résumé côté serveur, le miroir d'audit, les paquets de changements sur ses enregistrements,
  les règles d'écriture du dictionnaire (SEQUENCE, LOGIN, SYSDATE — le serveur décide), le Data Explorer.
  Deux choses qu'un enregistrement par une API demande, toutes deux en place : **les paramètres de l'écran
  accompagnent chaque enregistrement** (les valeurs avec lesquelles il a été lu, sous celles de la ligne —
  l'en-tête d'un formulaire de détail : le code produit et le type auxquels appartient une ligne de codes ;
  dans la grille, la fiche et l'enregistrement côté serveur), et **un paramètre peut être obligatoire**
  (« Obligatoire » dans l'éditeur du point d'accès) : un appel sans lui est refusé avant tout envoi, au lieu
  que le serveur prenne un blanc et agisse dessus.
- **L'application sur téléphone et tablette.** Sous 768 px : le menu est un tiroir ouvert par un bouton en haut à
  gauche, les boutons de l'en-tête ont leur ligne au-dessus des onglets (qui défilent), chaque boîte de dialogue
  est en plein écran, les marges des pages se réduisent — et **une grille s'affiche en liste** : chaque ligne une
  carte, chaque colonne une ligne « libellé · valeur », avec la recherche, la pagination, le menu de ligne et la
  modification en masse (le tri et les filtres d'en-tête restent un geste d'écran large). Un formulaire montre
  ses champs sur une colonne, ses onglets défilent. Au-dessus de 768 px rien ne change à la souris ; les
  éditeurs de paramétrage et la construction dans l'Explorateur restent des mises en page de bureau.
- Sur un téléphone, **la carte d'une ligne a un titre** : les deux premières colonnes côte à côte, en plus
  grand, puis quatre lignes, et « +N » ouvre le reste — une valeur vide ne donne pas de ligne, et un champ
  montré par son code et son libellé tient sur UNE ligne (le libellé en lecture, sa liste en modification en
  masse). Une colonne en décide autrement avec son nouveau réglage **Carte** (`card` : `title` = dans le titre,
  `show` = toujours une ligne, `hide` = jamais). Les filtres avancés prennent toute la largeur, le choix d'une
  période montre ses raccourcis sur une ligne au-dessus d'un seul mois, un champ ne fait plus zoomer la page
  sous iOS.
- **Sur une tablette en portrait** (768 à 1100 px), le menu est la barre d'icônes quel que soit le mode
  enregistré, ouverte par-dessus la page par son bouton (un doigt ne survole pas) et refermée en touchant
  ailleurs ; les boutons de l'en-tête prennent leur forme courte pour laisser leur place aux onglets.
- **Le menu a la largeur que vous lui donnez** : faites glisser son bord droit (180 à 460 px, retenu sur ce
  navigateur ; un double-clic rend la largeur par défaut). Et **le libellé d'une entrée de menu prend deux
  lignes** au lieu d'être coupé après quelques mots (son texte complet reste l'infobulle). La page d'un écran qui lit un point d'accès d'API porte le libellé de l'écran, et
  non la description du point d'accès (une phrase).
- Un écran sur un point d'accès d'API — **ses filtres avancés portent sur chaque colonne qu'il marque.** Un
  filtre n'était proposé que là où le point d'accès avait un paramètre de ce nom ; toute autre colonne marquée
  est maintenant filtrée sur les lignes renvoyées, avec les mêmes opérateurs que pour une requête (contient,
  égal, différent, commence / finit par, un intervalle) — et une colonne dotée d'une énumération ou d'une liste
  de valeurs y est une liste à choisir, cette liste pouvant elle-même lire un point d'accès. Un **paramètre à
  liste fermée de valeurs** (`choices`) est une liste déroulante au-dessus de la grille, et non un champ de
  saisie. (Un point d'accès dont le serveur plafonne les lignes est filtré dans ce qu'il a renvoyé.)
- Connecteurs API — **un paramètre nommé `username` ou `password` est celui du point d'accès, pas
  l'identifiant du connecteur.** `{{username}}` / `{{password}}` remplissent une demande de jeton avec les
  identifiants du connecteur ; un point d'accès qui DÉCLARAIT un paramètre de ce nom les recevait aussi quand
  il était laissé vide — un filtre d'utilisateur partait avec l'identifiant du connecteur (réponse : « aucun
  utilisateur »), et un mot de passe laissé vide aurait porté celui du connecteur. Un paramètre déclaré laissé
  vide est maintenant vide.
- Connecteurs API — **un serveur qui refuse quand il n'a rien à lister.** Le nouveau réglage « vide si »
  (`empty_on`) d'un point d'accès nomme les refus qui veulent dire « aucune ligne » — une partie de l'erreur du
  serveur (« no active users »), ou son statut HTTP (`404`) : l'écran montre alors une liste vide au lieu d'une
  erreur. Une erreur HTTP porte maintenant **les mots du serveur** quand les champs d'erreur du point d'accès
  les trouvent (« HTTP 500 — There are no active users… », et non un simple « HTTP 500 »). Et une liste qui
  n'est **pas là où le point d'accès le dit** (`rows_field`) est une erreur qui nomme ce que la réponse
  contient, au lieu d'un écran qui reste vide sans raison visible.
- Un serveur d'API injoignable est **dit avec son adresse** : « cannot connect to https://host:8999 — check the
  connector's base URL », là où le texte du pilote (« nodename nor servname provided ») ne nommait ni l'un ni
  l'autre.
- Un PDF demandé sur une machine où **la bibliothèque Pango manque** (WeasyPrint installé, `brew install pango`
  non fait) répond 503 avec ce qu'il faut installer, au lieu d'un 500 et d'une trace dans le journal.
- **Un écran lu par une requête peut enregistrer par les points d'accès d'un autre connecteur** — **Enregistre
  par** (`save_connector`, Écrans → Requêtes). L'écran garde sa requête, ses colonnes, son formulaire ; ses
  requêtes de modification / ajout / suppression nomment des points d'accès d'un connecteur API : l'enregistrement
  passe par l'application propriétaire des données (un formulaire JD Edwards : ses règles, ses valeurs par
  défaut, ses messages) et la lecture reste rapide. Une variable du point d'accès prend la valeur de la colonne
  de son nom, sinon de la colonne qui porte cet élément du dictionnaire — son `dd`, ou son nom sans le
  `dd_prefix` du connecteur (`ALPH` ← `ABALPH`) : aucune liste de correspondances à écrire. Tout ce qu'a
  l'enregistrement d'un écran reste vrai : la permission du point d'accès, `editable_when`, les règles de
  notification, la grille et le formulaire inchangés, le `screen_write` de l'assistant. L'intégrité, la
  recherche d'usages, le renommage et les packages suivent le connecteur d'enregistrement. Les actions
  d'enregistrement (`on_update`…) n'étaient pas le bon endroit : elles s'exécutent après un enregistrement que
  l'écran doit déjà savoir faire.
- Enregistrement par un point d'accès — **« non fourni » n'est pas « vide ».** Une colonne que l'écran n'envoie
  pas laisse la variable du point d'accès de côté (une fonction qui sert le point d'accès y reçoit `None` et ne
  touche pas au champ) ; une colonne envoyée vide est donnée vide — l'utilisateur l'a effacée. Avant, les deux
  arrivaient vides, et un champ que l'écran ne lisait pas aurait été effacé. Et pour une **modification** servie
  par une fonction, une colonne qui porte encore ce que la ligne avait au chargement est laissée de côté aussi :
  seul ce que l'utilisateur a changé est saisi (un formulaire recontrôle un champ ressaisi — JDE signalait un nom
  en double à chaque enregistrement). Un enregistrement **nomme aussi son
  écran** (`?_screen=&_app=`) : deux écrans qui enregistrent par le même point d'accès gardent chacun leurs
  règles et leurs colonnes.
- « Créer à partir de… » — **un générateur peut construire plusieurs points d'accès d'un coup** sans écran
  (`{endpoints: […]}`) : l'éditeur les ajoute tous à la liste.
- « Créer à partir de… » → un écran — **les points d'accès qui existent déjà** ne sont plus un refus : ils sont
  conservés tels quels (le nouvel écran les utilise) ou, sur demande, réécrits à leur place (`existing_endpoints`
  sur la route de création : `refuse` par défaut, `keep`, `replace`).
- Connecteurs API — **les points d'accès sous un titre.** Un point d'accès a un **Groupe** (`group`) :
  l'éditeur du connecteur range ensemble les points d'accès d'un même groupe sous ce titre, repliable, avec
  leur nombre ; la recherche y regarde aussi. Pour un connecteur qui porte les points d'accès de plusieurs
  applications ou sujets. Affichage seulement ; un point d'accès sans groupe reste en tête, comme avant.
- Connecteurs API — **un paramètre de requête vide peut ne pas être envoyé.** Un point d'accès dont les
  filtres optionnels passent dans la chaîne de requête les envoyait vides (`?instanceName=`) quand ils
  n'étaient pas renseignés, ce que certains serveurs refusent ou lisent comme un filtre sur rien. L'option
  « Ne pas envoyer un paramètre de requête dont la valeur est vide » de l'éditeur du point d'accès
  (`skip_blank_query`) n'envoie que ceux qui ont une valeur. Désactivée par défaut : les points d'accès
  existants ne changent pas.
- **Un générateur peut construire un écran entier.** « Créer à partir de… » écrivait un point d'accès. Le
  générateur d'un connecteur peut maintenant répondre par un écran : les points d'accès par lesquels il lit et
  enregistre, et l'écran lui-même. La boîte de dialogue demande alors l'application, l'identifiant et le titre
  de l'écran et s'il faut l'ajouter au menu, puis crée le tout en une passe validée (la route de l'Assistant
  d'écran, `POST /admin/assistant/scaffold`, qui accepte maintenant `endpoints`) : rien n'est écrit si quoi que
  ce soit est invalide ou si le nom d'un point d'accès est pris. Chaque point d'accès garde ce dont il a été
  construit : « Actualiser depuis le serveur » fonctionne dessus. Dans la liste, un point d'accès servi par une
  fonction affiche `FN` à la place d'une méthode.
- **Un point d'accès servi par une fonction.** Un seul appel HTTP ne peut pas tout faire : modifier une ligne
  qu'un serveur n'expose que par un formulaire, c'est la trouver, vérifier qu'il n'y en a qu'une, puis agir —
  plusieurs appels dans une session. Un point d'accès peut maintenant désigner une fonction du plugin d'une
  application (**Servi par une fonction**, `callable`) qui répond à la place d'un appel HTTP. Ses arguments
  sont les paramètres du point d'accès, ou l'objet JSON que donne le corps une fois ses `{{variables}}`
  remplies (arguments fixes et variables se mélangent, et la valeur d'un utilisateur reste dans sa chaîne) ;
  sa réponse est traitée comme une réponse — lignes, champs d'erreur et d'avertissement, champ et
  correspondance de réponse, « Modifie des données » — si bien qu'un écran, une action, un job ou l'assistant
  l'utilisent comme n'importe quel point d'accès. Seule une fonction découverte sous `plugins/` (`j_*`) peut
  être désignée ; une exception est l'échec de l'appel ; le rapport d'intégrité nomme un point d'accès dont la
  fonction n'est pas installée.
- **Un texte long garde ses lignes.** Une colonne au format Texte long (`textarea`) se saisissait dans une
  zone d'une ligne, dans la fiche comme dans le mode édition de la grille : ses retours à la ligne n'étaient
  pas affichés, et perdus dès que la valeur était modifiée. C'est maintenant une zone de plusieurs lignes dans
  les deux (celle de la fiche grandit avec le texte).
- **Une action de ligne dit ce qu'elle a fait.** Le message d'une étape `notify` dans une action du menu
  d'une ligne n'était écrit que dans la console du navigateur ; il s'affiche maintenant dans le bandeau d'état
  de la grille, comme celui d'une action de la barre d'outils.
- **Un champ de lignes `nom = valeur`, affiché en champs.** Un nouveau format, **Liste nom = valeur** (`pairs`) :
  dans la fiche, le texte est une liste de lignes nom / valeur (ajouter, retirer), le texte lui-même restant à
  un clic. Avec l'indication de colonne **`params_of`** — la colonne de l'écran qui nomme un point d'accès du
  connecteur de l'écran — il devient le formulaire de ce point d'accès : un champ libellé par paramètre
  déclaré, son aide en dessous, une liste pour ses valeurs fixes, la marque d'obligation ; un nom que le point
  d'accès ne déclare pas reste affiché. Pour tout écran qui garde les paramètres d'un appel à remplir.
- Une fiche ouverte depuis une autre fiche (la ligne d'une table imbriquée) **ne la déborde plus** : quand son
  contenu est plus haut que la fiche derrière, elle défile — son titre et son bouton Enregistrer restent
  visibles.
- `liberty.connectors.api_rows.answer_rows(endpoint, answer)` : les lignes de la réponse d'un point d'accès comme
  un écran les lit (`rows_field`, `row_map`), pour le code d'une application qui vérifie une réponse.
- **Un point d'accès servi par une action partagée — des étapes composées en un seul appel.** Un point d'accès
  d'API peut maintenant désigner une action partagée (**Servi par une action**, `action` ; elle se construit
  dans Paramètres → Actions) : ses étapes — requêtes, autres points d'accès, fonctions de plugin, conditions,
  boucles — s'exécutent sur le serveur et répondent comme UN point d'accès, qu'un écran, une action, un job,
  l'assistant ou un appelant extérieur utilise comme un autre. Les paramètres du point d'accès (ou l'objet JSON
  que donne son corps une fois rempli) sont les entrées de l'action (`INPUT.<nom>` ; une entrée non fournie
  prend la valeur par défaut de l'action) ; ce que l'action retourne (`return`) est la réponse, ses messages
  `notify` arrivent dans `messages` ; une étape en échec est l'erreur du point d'accès. **Elle s'exécute avec
  la permission du point d'accès** : qui détient `api:<connecteur>:<point d'accès>` exécute ses étapes, qui ne
  sont pas revérifiées pour l'appelant — un service composé s'accorde en entier, comme une orchestration. Le
  périmètre de données de l'appelant s'applique toujours à chaque requête. Une action qui appelle le point
  d'accès qu'elle sert est refusée ; le rapport d'intégrité nomme un point d'accès dont l'action n'est pas
  définie ; « Rechercher les utilisations » d'une action partagée liste les points d'accès qu'elle sert et son
  renommage les suit. Dans la liste des points d'accès, un tel point d'accès affiche `ACT`.
- **Une étape lit les lignes d'un point d'accès par nom de colonne.** Quand une étape `call_api` (dans une
  action d'écran ou une action partagée) appelle un point d'accès qu'un écran peut lire — qui dit où sont ses
  lignes (`rows_field`) et nomme ses colonnes (`row_map`) — elle récupère ces lignes : l'étape suivante lit
  `<étape>.first_row.<COLONNE>` et `<étape>.rows`, au lieu d'un chemin dans la réponse brute du serveur. Un
  point d'accès sans `rows_field`, ou avec un `response_field`, donne ce qu'il donnait. `POST /api/http/...`
  renvoie les mêmes lignes dans `rows`.
- **Le Studio — une application construite sans quitter la page.** Une nouvelle première page du menu
  Liberty, **Studio**. Choisissez une application : sa **carte** montre, côte à côte, son menu, ses écrans,
  les données que chaque écran utilise (requêtes et points d'accès) et les services que ses écrans appellent.
  Sélectionnez un élément et ce à quoi il est lié s'éclaire, les liens tracés d'une colonne à l'autre — une
  entrée de menu et l'écran qu'elle ouvre, un écran et ce qu'il lit, ce par quoi il enregistre et ce qu'il
  appelle, les écrans de sa fiche, un point d'accès et le service qui le sert — pendant que le reste s'estompe.
  Le panneau de droite dit ce qu'est l'élément et ce qui l'utilise, et **le modifie sur place** : le
  concepteur d'écran, l'éditeur de requête et le concepteur de service s'ouvrent par-dessus la carte et y
  reviennent (un double-clic sur un écran ou un service fait de même). Ce que rien n'utilise est montré comme
  tel : un écran qu'aucune entrée de menu ni aucune fiche n'ouvre, les requêtes et points d'accès de
  l'application qu'aucun écran n'utilise, une référence à ce qui n'existe plus. Une recherche resserre chaque
  colonne ; le résultat du contrôle d'intégrité est à un clic. `GET /admin/studio/map`,
  `GET /admin/studio/overview`.
  Une sélection suit les liens jusqu'au bout : une requête éclaire les écrans qui l'utilisent et leurs entrées
  de menu, une entrée de menu tout ce à quoi son écran mène. Un **point d'accès** se modifie aussi sur place
  (l'éditeur de la page du connecteur, dans une fenêtre), comme le **menu** et les **tableaux de bord** (leurs
  pages, ouvertes par-dessus la carte).
- **Les jobs et les points d'accès se modifient comme les services : le flux est l'espace de travail.**
  L'**éditeur de job** est maintenant le flux du job — le début, une carte par étape avec sa nature, son nom et
  ce qu'elle fait, la fin — avec ce qui est choisi modifié à côté : les champs d'une étape (la déplacer, la
  dupliquer, la supprimer s'y trouvent), ou le job lui-même au début (son planning, ses préréglages, ses
  reprises, ses alertes, ses paramètres). Un **+** sur un lien insère une étape à cet endroit (une étape ne
  pouvait s'ajouter qu'à la fin). L'**éditeur de point d'accès** montre un point d'accès en quatre temps — ce
  qu'il est → ses entrées → l'appel (la requête HTTP, ou la fonction ou le service qui le sert) → la réponse
  (où sont ses lignes, ses colonnes) — et les champs du temps choisi à côté. Un **paramètre** est une ligne
  (son nom, son libellé, sa valeur par défaut, « obligatoire ») qu'on ouvre pour modifier ses champs libellés,
  au lieu de deux rangées de cases sans libellé.
- **Une seule présentation pour les champs de tous les éditeurs.** Les champs d'un connecteur d'API, d'un point
  d'accès, d'un job et d'une étape de job portent maintenant leur libellé au-dessus du champ, comme une étape
  de service et le concepteur d'écran.
- **Tests : les tests de non-régression d'une application, dans le framework.** Une nouvelle page,
  Construire → **Tests**. Une suite désigne un connecteur d'API et contient des cas ; un **cas est dessiné
  comme un flux d'appels** — chaque carte le point d'accès appelé et ce qui est attendu, colorée par son
  dernier résultat — et ce qui est choisi se modifie à côté : le cas, ou une étape — son point d'accès (la
  liste du connecteur), ses paramètres dans le formulaire du point d'accès, ce qu'elle attend (l'appel
  fonctionne ou doit être refusé, un nombre de lignes, les valeurs de la première ligne par nom de colonne ;
  les chemins dans la réponse brute sous Avancé). **Remplir depuis la réponse** joue l'appel et propose ce
  qu'il faut attendre. **Jouer ce cas** / **Jouer la suite** vérifient les cas et les comparent à l'exécution
  de référence ; **Enregistrer la référence** garde ce que les étapes gardent (`keep`) ; un cas qui modifie
  des données est ignoré sauf si l'exécution l'autorise. Les suites sont des fichiers TOML
  (`<config>/tests/<suite>.toml`, empaquetés et versionnés avec la configuration), les dernières exécutions de
  chaque cas et la référence sont gardées à côté. `liberty.apptests` (exécution, stockage), `/admin/tests`.
  Né comme les tests JD Edwards de l'application nomajde ; rien n'y est propre à JD Edwards, et les points
  d'accès de n'importe quelle application se testent.
- **Résultats des tests.** Exploiter → **Résultats des tests** : combien de cas réussis, en échec, modifiés ou
  jamais joués, et par suite ses dernières exécutions en barres (la plus récente à droite) avec qui les a
  lancées et le temps pris, puis chaque cas avec ses derniers résultats en bande, sa dernière exécution, sa
  durée et ce qui a été trouvé ; un cas s'ouvre dans la page Tests. La vue d'ensemble montre les mêmes totaux
  et y mène. `GET /admin/test-results`.
- Le formulaire de lancement d'un job peut lister les suites de tests : `choices_from = "test_suites"` (ou
  `"test_suites:<connecteur>"`).
- **Les tests ont leurs propres permissions**, pour qu'une équipe fonctionnelle les utilise sans être
  administratrice : `tests:view` (les suites et les résultats), `tests:run` (les jouer) et `tests:edit` (les
  écrire) ; chacune inclut la précédente, un superutilisateur les détient toutes. Elles sont dans l'éditeur de
  rôles (Autres accès → Tests), et le menu d'une application peut porter les deux pages — `type = "page"`,
  `target = "/liberty/test-results"` ou `"/liberty/tests"` — montrées à qui détient `tests:view`.
- **Le menu Liberty, dans l'ordre où les choses se construisent.** Construire avait quinze entrées ; c'est
  maintenant **Construire** (Studio, assistant), **Données** (connecteurs, dictionnaire), **Écrans** (écrans,
  menus), **Logique** (services, tests, notifications, IA) et **Analyse** (tableaux de bord, graphiques,
  explorateur, modèles de rapport). L'intégrité ouvre le dossier Déployer : vérifier, puis livrer.
- **Les longs flux passent à la ligne, et se zooment.** Le flux d'un service, d'un job et d'un point d'accès
  continue sur la ligne suivante au lieu de partir à droite, et chaque canevas a un zoom (− / % / +, gardé par
  nature de flux sur l'appareil).
- **Le tableau de l'Explorer est la grille commune, et le résultat peut prendre la page.** La vue tableau et
  les lignes de détail sont dessinées par la grille des écrans de données : recherche, un filtre par colonne,
  tri sur plusieurs colonnes, regroupement, choix des colonnes et son propre export CSV / Excel de ce qui est
  affiché ; le code d'une colonne reste à côté de son en-tête ; une ligne ouvre toujours son détail, une ligne
  de détail sa fiche. Le détail reste paginé par le serveur — la pagination de la grille montre la page et le
  total du serveur (`serverPaging` sur la grille commune). Un bouton **Définition** à côté du choix de
  l'analyse masque les champs et les étagères : le résultat prend la page, avec la vue, la période, le Top et
  Exécuter sur une ligne. Une analyse enregistrée s'ouvre ainsi, une nouvelle sur sa définition ; le bouton
  reste à la même place dans les deux états. Un tableau dans une tuile de tableau de bord est la même grille
  sans sa barre d'outils.
- **Une application peut livrer sa propre page.** Un écran configuré est une grille et un formulaire ; ce qui
  n'est ni l'un ni l'autre — une structure à parcourir, un parcours guidé — une application le livre désormais
  comme une page : un module JavaScript construit par page, `plugins/<app>/pages/<page>.js`. Une entrée de menu
  de type `page` dont la cible est `/app/<app>/<page>` l'ouvre dans un onglet ; le cadre sert le module
  (`GET /api/pages/<app>/<page>`, listé par `GET /api/pages`) et lui remet ce dont ses propres pages sont
  faites — React, les composants communs, le client d'API, le thème, un `call` qui exécute une fonction de
  l'application — si bien que la page n'importe rien et ressemble au reste. La permission est
  `page:<app>:<page>`, vérifiée avant de chercher le fichier et demandée aussi à l'entrée de menu ; l'éditeur de
  rôles la connaît. Le contrat est `frontend/src/pages/AppPage/sdk.ts` (version 1 : des champs s'ajoutent, aucun
  n'est renommé).
- **Une entité de l'Explorer peut être un point d'accès d'API.** Une entité était une requête ou une table d'un
  connecteur SQL ; elle peut maintenant désigner un point d'accès à lignes d'un connecteur d'API
  (`connector = "<connecteur d'API>"`, `query = "<point d'accès>"` — ceux qu'un écran sait lire). Ses champs sont
  découverts depuis les colonnes du point d'accès, libellés par le dictionnaire comme ceux d'un écran ; le moteur
  lit les lignes — sous le périmètre de données de l'appelant — et les filtre, regroupe, mesure, trie et pagine
  lui-même : la page, les analyses enregistrées, les tableaux de bord, les exports et les outils de l'assistant
  fonctionnent dessus sans changement. Un filtre « égal » sur un champ qui est un paramètre du point d'accès lui
  est aussi envoyé (un serveur qui ne liste rien sans lui, ou qui réduit ce qui est lu). Ce qu'une telle entité
  ne peut pas faire : être jointe à une autre entité, et porter un champ calculé — les deux sont refusés avec
  la raison. Chaque demande appelle le point d'accès : le coût est celui de la lecture, plafonnée à 50 000 lignes.
- **Une analyse ou un tableau de bord de l'Explorer va dans un menu.** Sur une analyse ou un tableau de bord
  enregistré et partagé, **Ajouter à un menu** (pour un super-utilisateur : les menus sont de la configuration)
  demande le menu de quelle application, quel dossier et quel libellé, et ajoute l'entrée. Un clic dessus ouvre
  l'Explorer sur cette analyse — son résultat aussitôt, la définition à un clic — ou sur ce tableau de bord.
  L'entrée est montrée à qui peut utiliser le modèle (`explorer:<app>:<modèle>`), ce que demande désormais aussi
  un lien vers un modèle entier (`/explorer/<app>/<modèle>`) ; un objet privé est refusé tant qu'il n'est pas
  partagé ; une entrée dont l'analyse a été supprimée le dit. Route : `POST /admin/menus/publish` ; cibles
  `/explorer/<app>/<modèle>/a/<id>` et `…/d/<id>`.
- **Un point d'accès généré se modifie là où il a été créé.** « Créer depuis… » rendait un point d'accès facile à
  ajouter ; lui ajouter ensuite une colonne ou un paramètre demandait de modifier à la main son corps, sa
  correspondance de colonnes et ses paramètres. **Modifier…** sur un point d'accès généré rouvre le dialogue sur
  ce dont il a été construit, ses choix tels qu'ils étaient — cochez une colonne, un champ, un filtre — et ce que
  le générateur construit est fusionné avec ce que vous avez : votre nom, votre libellé et les textes de vos
  paramètres sont gardés, les différences sont listées avant toute application, et les points d'accès lire /
  ajouter / modifier / supprimer d'une application suivent ensemble. Pour tout point d'accès, les colonnes de ses
  lignes sont maintenant une liste (une colonne, la clé d'une ligne dont elle est lue) au lieu d'une ligne de
  texte, et **Colonnes depuis une réponse** appelle le point d'accès une fois et propose à cliquer les clés de sa
  première ligne.
- **Un lien peut ouvrir une page sur quelque chose.** `/job/<id>?<paramètre>=<valeur>` ouvre la page d'un
  traitement avec ces valeurs remplies ; `/liberty/connectors?connector=<nom>` ouvre la page Connecteurs sur ce
  connecteur, et avec `&create=<générateur>&q=<texte>` son dialogue « Créer depuis… » sur ce générateur, le texte
  déjà cherché. Pour une page livrée par une application, une notification, un document. Le contrat des pages
  gagne `useCan` (l'appelant détient-il une permission — pour ne montrer un bouton qu'à qui peut s'en servir).
- **L'intégrité et « Rechercher les utilisations » lisent les procédures de l'assistant.** Une procédure
  (`ai.toml`) est un texte que l'assistant suit — « query `list_runs` on the `nomaflow` connector » — donc une
  requête ou un point d'accès qu'elle nomme sera appelé par l'assistant ; rien d'autre dans la configuration ne
  le désigne, et l'intégrité signalait une telle requête comme *inutilisée*, la recherche d'utilisations comme
  « 0 référence — suppression sans risque ». Les deux comptent maintenant un nom écrit entre accents graves
  dans une procédure lorsqu'il est une requête ou un point d'accès du connecteur écrit avec lui
  (`` `nomaflow.list_runs` ``), de l'application de la procédure, ou d'un connecteur que le texte nomme ; le
  connecteur est lui aussi utilisé. Dans l'autre sens, une nouvelle erreur, **Assistant playbook** : une étape
  qui dit « query `x` » alors qu'aucun connecteur que la procédure peut désigner n'a de requête ou de point
  d'accès de ce nom — l'étape échouerait.
- **Une page livrée par une application peut utiliser la grille partagée.** Le contrat des pages (version 2)
  gagne `Grid` : la grille des écrans de données — recherche, filtres par colonne, tri, regroupement, choix des
  colonnes, export, vues enregistrées de l'utilisateur — pour une liste de lignes que la page lui donne. La page
  décrit ses colonnes dans une petite forme propre au contrat (`{ key, label, type?, mono?, muted?, value?,
  render? }` : une cellule peut dessiner un lien ou une étiquette, la colonne trie, filtre et exporte toujours
  sur sa valeur) et donne à la grille une boîte qui a une hauteur. Chargée avec la première page qui en dessine
  une : le paquet d'entrée ne grossit pas.
- **Une requête et un connecteur d'API se dessinent en étapes.** Une requête, une séquence ou une liste de
  valeurs s'ouvre en trois cartes — **Ce que c'est** (libellé, description), **Instruction** (son premier mot,
  les bases pour lesquelles elle est écrite, en orange quand elle est vide ou qu'elle écrit sans être marquée
  modifiable) et **Paramètres** (combien sont déclarés, les `:paramètres` de l'instruction) — chacune montrant
  sa partie du formulaire ; elles remplacent les onglets du formulaire. Les six onglets d'un connecteur d'API
  sont les mêmes cartes : l'hôte du serveur, le type d'authentification et ce qui lui manque encore, les
  environnements, les points d'accès et combien écrivent, les webhooks, le test. La ligne d'un point d'accès a
  un bouton qui ouvre son éditeur sur toute la fenêtre — pour celui qui a beaucoup d'étapes ou un long corps ;
  c'est le même éditeur sur le même brouillon, écrit par Enregistrer de la page.
- **Une analyse exportée par le serveur suit libellé / code / les deux.** Les exports Excel et PDF — les
  boutons de la page, un tableau de bord d'analyses, l'étape rapport d'un traitement — écrivaient les codes
  bruts. Ils écrivent maintenant ce que l'analyse montre : le libellé par défaut, le code, ou les deux (deux
  colonnes dans un tableau, `code · libellé` dans un tableau croisé ou un graphique). Une liste de valeurs est
  lue avec la permission et le périmètre de l'appelant ; celle qui ne peut pas être lue laisse les codes.
  **Un rapport planifié d'une analyse ayant une colonne codée montre donc des libellés à partir de cette
  version** — réglez le champ sur *Code* pour garder les codes.
- **La page Tests montre les boutons que l'appelant peut utiliser.** Enregistrer et les boutons de création /
  suppression demandent `tests:edit` ; Exécuter, Enregistrer la référence et « Remplir » demandent `tests:run`
  (ou `tests:edit`). Un utilisateur qui ne peut que lire n'en voit aucun, et une ligne le dit. Les routes
  refusaient déjà ; la page ne propose plus ce qu'elles refusent.
- **Une colonne codée d'une analyse montre son libellé, son code, ou les deux.** Dans l'Explorer, la pastille
  d'un champ lu par une énumération ou une liste de valeurs porte le choix : **Libellé** (comme avant),
  **Code + libellé**, **Code**. Dans le tableau, *Code + libellé* donne deux colonnes comme sur un écran de
  données — `Société (ID)` puis `Société` — chacune triée, filtrée et exportée pour elle-même ; le tableau
  croisé écrit `101 · Alpine Foods SA` dans son unique cellule de libellé, et un graphique garde les libellés
  sur son axe sauf si le champ est réglé sur son code. Le choix est enregistré avec l'analyse et suivi par les
  tuiles des tableaux de bord. Deux conséquences d'une lecture unique pour toutes les vues : le tableau croisé,
  qui montrait les codes bruts, montre maintenant les libellés par défaut, et un code qui est un nombre (un
  numéro de société) est lu par sa règle dans le tableau aussi, au lieu d'être écrit comme un chiffre.
- **Un tableau de bord d'analyses se met en page comme les tableaux de bord.** Dans **Modifier la
  disposition** de l'Explorer, une tuile se déplace par glisser-déposer — sa poignée, une barre montrant où elle
  arrive — et se dimensionne par son coin : des colonnes entières (1 à 4) et la plus proche des trois hauteurs.
  La poignée prend aussi le clavier (flèches : déplacer ; Maj + flèches : dimensionner). Les quatre petits
  boutons (gauche, droite, largeur, hauteur) disparaissent.
- **La pagination d'une grille compte tout le résultat.** À partir de la deuxième page, la ligne sous une
  grille disait « 51–50 sur 50 » : son total était celui des lignes de la page affichée. Elle compte maintenant
  toutes les lignes réparties entre les pages, après les filtres et le regroupement (« 51–100 sur 5000 »).
- **Un export coupé par le Top de l'analyse le dit.** Un tableau limité à ses 20 premiers groupes se
  terminait, dans le classeur et le PDF, par « truncated at 3000 rows » — le plafond de l'export, qui n'était
  pas atteint. Il dit maintenant « 20 premiers groupes sur 44 » ; la ligne des 3000 lignes reste pour le
  plafond de l'export. La ligne du classeur suit la langue du lecteur comme celle du PDF.
- **La page Explorer a l'allure des éditeurs.** Ce dont une analyse est faite se lit en cinq cartes, chacune
  avec son icône et son titre en couleur — **Vue**, **Période**, **Lignes**, **Mesures**, **Filtres** (et
  **Options** pour un tableau croisé ou un graphique) — avec ses pastilles dans sa couleur ; le résultat est un
  panneau dont l'en-tête dit la vue et ce que l'exécution a répondu (groupes, lignes, troncature) ;
  Enregistrer, Enregistrer sous, Supprimer, Excel et PDF sont dans l'en-tête de la page à côté du choix du
  modèle. Les tableaux de bord d'analyses prennent la même carte de période. Rien ne se comporte autrement :
  les champs se cliquent ou se glissent toujours sur une étagère, les pastilles se réordonnent toujours en
  glissant. L'en-tête du tableau de résultat et du tableau croisé cache les lignes qui défilent dessous (son
  fond laissait voir au travers).
- **Un modèle de l'Explorer est dessiné.** L'éditeur de l'Explorer montre le modèle en schéma : le pivot, les
  entités qu'il atteint placées selon leur distance, chaque relation un trait portant les colonnes de la
  jointure — et, à part en orange, les entités qui n'atteignent **pas** le pivot (une analyse ne peut pas les
  utiliser) et les relations qui n'ont pas encore de paire de colonnes. Un clic sur une entité montre ce
  qu'elle est (sa requête ou sa table, sa clé, ses mesures), ses relations, et Modifier, En faire le pivot,
  Supprimer ; un clic sur un trait modifie les paires de colonnes de cette relation ; sans rien de choisi, le
  panneau porte les réglages du modèle (libellé, pivot, description, préréglage). La disposition est calculée
  à partir du modèle — rien n'est placé à la main — et le canevas se zoome.
- **Un écran qui lit ou enregistre par une API voyage en entier.** Dans un paquet et dans l'historique d'un
  écran, les points d'accès qu'un écran lit (ses lignes) ou par lesquels il enregistre (ceux de son
  connecteur, ou de son connecteur « Enregistre par ») étaient cherchés comme des requêtes SQL sur le
  connecteur d'API, non trouvés, et listés comme **références manquantes**. Ils sont maintenant ce qu'ils
  sont — des points d'accès d'API de l'écran : listés sous « Points d'accès API », cochés par défaut comme ses
  requêtes, et emportés dans le paquet ; un point d'accès servi par une action partagée emmène cette action.
  Un écran enregistré désormais les consigne dans son historique (une entrée plus ancienne garde ce qu'elle
  avait consigné).
- **Menus : glisser une entrée là où elle va.** Dans l'éditeur de menu, une ligne se glisse sur une autre :
  au-dessus, en dessous, ou dedans quand c'est un dossier — avec tout ce qu'elle contient ; un trait ou une
  surbrillance montre où elle se posera. Chaque ligne a l'icône de ce qu'elle ouvre (écran, tableau de bord,
  traitement, requête, point d'accès, page), l'arbre et l'entrée choisie sont deux panneaux qui commencent sur
  la même ligne, les actions de l'entrée (Cloner, Rechercher les usages, Rechercher les dépendances) sont sur
  son panneau, et Dossier, Élément, Annuler et Enregistrer sont dans l'en-tête de la page.
- **Connecteurs : le SQL est là où l'on regarde.** Avec une table ou une requête ouverte, les sections du
  connecteur et les étapes de la table se replient sur une ligne chacune et les actions de la table sur une
  rangée d'icônes : l'instruction commence bien plus haut dans la page. Les dialogues **Construire un
  SELECT / INSERT / UPDATE / DELETE** sont plus larges et leur aperçu prend la hauteur que le dialogue laisse,
  au lieu d'une petite boîte au-dessus d'un vide.
- **Changements, Notifications, IA : le même cadre.** Changements : les paquets et le paquet choisi sont deux
  panneaux (la liste défile seule), Nouveau paquet et Actualiser sont dans l'en-tête. Notifications : Ajouter
  une règle est dans l'en-tête, la liste commence donc sur la même ligne que la règle ; une page vide dit
  comment commencer. IA : Annuler et Enregistrer sont dans l'en-tête.
- **Les notes de version se lisent une version à la fois.** Les versions sont un panneau à gauche, chacune
  avec son nombre d'entrées ; la version choisie montre chaque entrée comme une carte titrée par la phrase qui
  l'ouvre, sur toute la largeur du panneau. Une recherche trouve une entrée par ses mots **dans toutes les versions**
  et dit à quelle version elle appartient.
- **Tableaux de bord, Graphiques, Explorer, Modèles de rapport : les boutons là où les autres les ont.** Ajouter
  un tableau de bord, Ajouter un graphique, Ajouter / Supprimer un modèle et Enregistrer, Nouveau modèle sont
  dans l'en-tête de la page à côté de « Recharger la config » ; les messages sont des bandeaux en dessous.
- **Historique et Rapports de documentation : le même cadre.** Historique : les fichiers, écrans et mises à
  niveau sont un panneau comme toutes les listes des éditeurs, qui défile seul. Rapports de documentation : la
  carte Réglages a la hauteur de la carte Contenu à côté d'elle.
- **La page Connecteurs, côté SQL : plus rien ne cache la liste.** La ligne d'un connecteur dit ce qu'il
  contient (`sql · 117 tables · 34 requêtes`, `api · 68 points d'accès`). Ses sections sont des cartes
  d'étape avec leur compte — Réglages (son pool), Tables, Requêtes, Séquences, Listes de valeurs. **Ouvrir une
  table garde la liste à côté** : la liste se réduit à une colonne (les quatre instructions de chaque table en
  points) et la table s'ouvre à côté, si bien que passer d'une table à la suivante est un clic ; de même pour
  une requête, une séquence, une liste de valeurs. Une table est dessinée en étapes — Général, puis **Lecture,
  Mise à jour, Insertion, Suppression** — chaque carte disant si l'instruction est réglée, et en orange quand
  une instruction d'écriture est là sans être marquée inscriptible (elle serait refusée à l'exécution).
  Connecteur, Annuler et Enregistrer sont dans l'en-tête de la page. Les champs sont ceux qu'ils étaient.
- **La page Dictionnaire : un enregistrement dit ce qu'il est dans la liste.** La ligne d'une entrée porte son
  format et sa règle (`LOOKUP` — sa cible dans l'infobulle de la pastille), celle d'une énumération le nombre de ses valeurs, celle d'une liste
  de valeurs ou d'une séquence l'autre connecteur où elle lit ; le filtre trouve un enregistrement par son nom, son
  libellé ou l'un de ces éléments (saisir `lookup` montre toutes les entrées réglées par une liste de
  valeurs). L'onglet de chaque type dit combien d'enregistrements le périmètre contient, le bouton
  **Rechercher les usages** de l'enregistrement choisi dit en combien d'endroits il est utilisé, la liste est
  un panneau qui commence sur la même ligne que le formulaire, et Analyser une table, Exporter, Annuler et
  Enregistrer sont dans l'en-tête de la page. Le formulaire d'un enregistrement est inchangé.
- **Tableaux de bord : les cartes se déplacent par glisser-déposer et se dimensionnent par leur coin.** Dans
  l'éditeur de tableau de bord, la poignée d'une carte se glisse sur une autre carte — une barre montre de
  quel côté elle se posera — et son coin inférieur droit la dimensionne, la carte suivant le pointeur en
  colonnes et lignes entières avec sa taille écrite près de la poignée (`6 × 2`). Les huit petits boutons
  (L− L+ H− H+ ← →) ont disparu ; la poignée prend le clavier à leur place : les flèches déplacent la carte,
  Maj + flèches la dimensionnent.
- **La page Pools : une adresse saisie comme une adresse, et une connexion que l'on peut tester.** La ligne
  d'un pool dans la liste dit à quoi il se connecte (`postgresql · hôte:5432 / base`). Le pool choisi est
  dessiné en étapes — **Base de données**, **Compte**, **Schémas**, **Liens de base**, **Réglages fins** —
  chaque carte disant ce qu'elle contient. L'adresse de la base se saisit selon ce que son type demande, sur
  une ligne : le **type de base**, l'hôte, le port et le seul nom qui dit quelle base — une base, un **nom de
  service** Oracle (un SID quand l'URL est écrite avec), un fichier SQLite. Choisir un autre type y fait passer
  le pool : son pilote, son port habituel, et le nom porté d'une base à un nom de service ou l'inverse. Le
  pilote (une liste), les options et l'URL SQLAlchemy — composée à partir des champs, toujours modifiable —
  sont repliés sous **Avancé**. Une URL que la
  page ne sait pas lire champ par champ (tout ce qu'elle ne saurait pas réécrire à l'identique) est montrée
  seule et jamais réécrite ; les références `${VAR}` sont gardées telles quelles. Un mot de passe écrit dans
  l'URL est signalé, avec un bouton qui le déplace dans le champ mot de passe chiffré. **Tester la connexion**
  ouvre une connexion avec le pool tel qu'il est sur la page — enregistré ou non — et répond par le temps pris
  et la version du serveur, ou par les mots du pilote (`POST /admin/config/pools/test` ; rien n'est
  enregistré, les pools en service ne sont pas touchés). Ajouter, Annuler et Enregistrer sont dans l'en-tête
  de la page.
- **La page App est une liste de sections avec, à côté, celle qui est choisie.** Les dix à treize accordéons en
  ordre alphabétique sont maintenant quatre groupes dans l'ordre où une installation se règle —
  **L'installation** (serveur, affichage, adresses externes, charte des PDF, licence), **La connexion**
  (comptes, OpenID Connect), **Ce qui est activé** (assistant IA, notifications par e-mail, nomaflow, paquets
  de changements), **L'exploitation** (journal du serveur, ce que le déploiement fixe) — chaque ligne disant
  son état (le modèle, « désactivé », « pas de SMTP », « redémarrage après un changement »). Une section est
  ouverte à la fois, sur toute la hauteur ; la page retient laquelle. Chaque réglage est toujours là, et **les
  champs s'alignent** : une seule grille de colonnes pour toutes les sections, chaque libellé au-dessus de son
  champ, à la place de lignes de champs dimensionnés un par un. Un libellé redevient court : l'explication
  qu'il portait entre parenthèses est une ligne sous le champ, en texte simple.
- **Enregistrer et Annuler sont dans l'en-tête de la page**, à côté de « Recharger la config », sur les pages
  App et Thème (et sur chaque éditeur au fur et à mesure qu'il est refait) — plus sur une ligne à part dans la
  page (`common/HeaderActions` : une page montrée sans en-tête les garde sur une ligne).
- **La page Thème montre ce qu'elle change.** Les palettes sont des pastilles à cliquer, les polices des cartes
  écrites dans leur propre police, et l'aperçu est un petit morceau de l'application — son menu, des chiffres,
  une grille avec une ligne choisie, un champ actif, les boutons — dessiné **en sombre et en clair** côte à
  côte, quel que soit le mode où vous êtes. Les deux listes natives ont disparu. **Avancé : variables CSS**
  modifie les variables brutes de `theme.toml` (`[theme.vars]`), que la page ne montrait pas — et **perdait à
  chaque enregistrement** : une variable réglée à la main est maintenant conservée, listée et modifiable.
- **L'intégrité et « Rechercher les usages » voient ce que nomment les traitements, les modèles de l'Explorer
  et les règles de notification.** Une requête que seul un traitement exécute (la requête d'une étape, le
  `refuse_query` d'un chargement de fichier, les choix d'un paramètre de lancement), qu'une entité de
  l'Explorer lit ou que l'action d'une règle de notification appelle était signalée **inutilisée** — et
  « Rechercher les usages » répondait « 0 référence », une invitation à supprimer une requête dont un
  traitement a besoin. Ce sont maintenant des références, chacune listée avec son traitement et son étape, son
  modèle et son entité, ou sa règle ; un connecteur que seul un traitement nomme (la source ou la cible d'une
  copie, un connecteur passé à une étape python) n'est plus « inutilisé » non plus. Les requêtes que « Analyser
  une table » du dictionnaire lit par leur nom comptent comme utilisées par lui. **Une liste de valeurs qui lit
  un point d'accès d'API** (ses lignes, comme un écran les lit) n'est plus une erreur « requête manquante » ; un
  point d'accès qui n'existe pas, ou qui ne renvoie pas de lignes, en est une. « Rechercher les usages » sur un
  point d'accès liste aussi les écrans qui le lisent ou enregistrent par lui, les listes de valeurs et les
  règles de notification.
- **Le concepteur d'écran se lit comme l'écran.** Ses neuf onglets sont maintenant **six étapes dessinées en
  flux** — Ce qu'il est → Données → Grille → Fiche → Actions → Sortie — et chaque carte dit ce que l'écran y
  contient : la requête qu'il lit et par combien il enregistre (en orange quand aucune n'est réglée), les
  colonnes et les filtres, les onglets et les champs de la fiche, les actions de la barre d'outils et du menu
  de ligne, l'export. Une étape à plusieurs sections les montre en dessous (Grille : Colonnes, Vues,
  Synthèse ; Actions : Actions, Menu de ligne). Chaque section est celle qu'elle était : aucun réglage n'a
  bougé ni disparu.
  **Les colonnes sont une liste avec, à côté, les réglages de la colonne choisie**, comme un service montre
  son flux et l'étape choisie : chaque ligne donne le nom de la colonne, son libellé (celui de l'écran, sinon
  celui du dictionnaire) et ce qui mérite d'être vu sans l'ouvrir — clé, filtre, masquée, obligatoire,
  lecture seule, sensible, sa règle. Glisser pour réordonner, filtrer la liste, ajouter, retirer ; les
  réglages sont le même formulaire, ses règles imbriquées s'ouvrent dans le panneau.
- **Une page de vue d'ensemble.** Le menu Liberty s'ouvre maintenant sur **Vue d'ensemble** : les applications
  de l'installation, chacune avec ses chiffres et ce qui y est détaché (elle s'ouvre dans le Studio sur cette
  application), et **comment une application se construit** — huit étapes dans l'ordre (connecter les données,
  nommer les champs, construire les écrans, organiser le menu, ajouter la logique, montrer les chiffres, donner
  l'accès, vérifier et livrer), chacune avec une phrase, ce que l'installation en contient déjà et le chemin
  pour y aller. « Un nouvel écran, avec l'assistant » part de là.
- **Les services, et leur flux.** La page Actions devient **Services** : une action partagée est un service.
  Son concepteur s'ouvre sur une **vue graphique** — les étapes dessinées comme un flux lu de gauche à droite :
  le début avec les entrées, une carte par étape (requête, point d'accès, fonction, service, message…), une
  condition qui se sépare en voies Vrai et Faux, une boucle ou un groupe qui encadre ses étapes, la réponse à
  la fin. Un clic sur une carte modifie cette étape dans le panneau latéral (sa cible, la correspondance de
  ses entrées) ; un **+** sur un lien ajoute une étape à cet endroit ; l'ancienne liste reste à un clic
  (Flux / Liste). La disposition suit la structure de l'action : rien n'est placé à la main.
  Sur chacun : **Exécuter** — remplir ses entrées, voir la réponse qu'il donnerait comme point
  d'accès, les étapes exécutées et le temps pris (les étapes s'exécutent réellement) ; **Exposer** — créer le
  point d'accès d'API qu'il sert, sur un connecteur d'API de l'installation ou sur celui du studio (`studio`,
  créé au premier usage), avec les entrées du service comme paramètres du point d'accès (une entrée lue par les
  étapes sous `INPUT.<nom>` compte même si l'action ne la déclare pas). Les points d'accès qu'un service sert
  déjà sont listés sur lui. Routes : `GET /admin/studio/services`, `POST /admin/studio/run`,
  `POST /admin/studio/expose` (superutilisateur).

## 7.0.83 — 2026-11-19

- **Corrections d'affichage après la passe de documentation.** Les réglages de l'extension Claude Desktop
  (adresse, jeton, dossier de fichiers, certificat) et sa description sont en français quand le profil est en
  français au téléchargement. Surveillance → Usage de l'IA : le coût estimé suit la langue (`38,20 $` en
  français) et la liste s'intitule *Dernière activité* (elle contient aussi les cartes appliquées et les appels
  MCP). Un paquet de modifications affiche « Assistant IA » pour les écritures de l'assistant. Le réglage
  *Outils d'écriture* et la permission `ai:write` disent qu'ils couvrent aussi les actions d'un écran et le
  lancement d'une tâche. L'onglet Jetons d'accès dit ce que fait **Mettre fin à l'accès** pour un client
  connecté par authentification. Dans ces notes, chaque version publiée porte sa date au lieu de « non publiée ».

## 7.0.82 — 2026-11-19

- **Assistant IA : plusieurs enregistrements sur une seule carte — `screen_write_many`.** « Crée ces cinq
  utilisateurs », « passe ces douze lignes au statut validé » : l'assistant propose une carte qui liste tous
  les enregistrements (50 au plus, d'un même écran) au lieu d'une carte par enregistrement, et un seul
  Appliquer les enregistre dans l'ordre, chacun exactement comme `screen_write` (règles, listes de valeurs,
  hooks, audit, paquet de modifications, verrou d'enregistrement). Tous les enregistrements sont contrôlés avant
  d'écrire le premier : si l'un est refusé, rien n'est écrit et la réponse dit lequel et pourquoi. Si une
  écriture échoue malgré tout en cours de route, le traitement s'arrête là et indique combien sont écrits.
  Même option et mêmes permissions que `screen_write`.
- **MCP : les clients connectés par authentification sont listés, et l'on peut mettre fin à l'un d'eux.** Un
  client connecté par OAuth (Claude Desktop, Claude Code, claude.ai) détenait des jetons que personne ne
  pouvait reprendre, sauf à changer le mot de passe — ce qui les terminait tous. Chaque consentement est
  désormais enregistré (`ly2_oauth_grant`, sur le pool d'authentification : le nom du client, la date de
  connexion, le dernier usage, le jour de fin — jamais un jeton) : Profil → Jetons d'accès liste les vôtres
  avec **Mettre fin à l'accès**, refusé dès l'appel suivant du client (`GET /oauth/grants`,
  `DELETE /oauth/grants/{id}` ; un super-utilisateur voit ceux de tous avec `?everyone=1`). Un client connecté
  avant cette version continue de fonctionner jusqu'à la fin de son accès, sans figurer dans la liste ; se
  reconnecter pour l'y voir.

- **MCP : plusieurs serveurs Liberty dans un même Claude Desktop.** Claude Desktop distingue les extensions par
  leur nom, et toutes les extensions Liberty portaient le même : installer l'extension d'un second serveur
  (production et test, un autre client) remplaçait la première. L'extension porte désormais le nom de son
  instance — l'hôte de son adresse et l'environnement de l'installation (`[app] environment`, Administrer →
  App) : « Liberty Next – Test · liberty-test.acme.com » — elles s'installent donc côte à côte ; le fichier
  téléchargé porte le même nom. Le serveur indique aussi de quelle instance il s'agit, dans son titre et en tête
  de ses instructions à Claude (« utilise les outils de l'instance que l'utilisateur nomme, et dis quelle instance
  une réponse ou une modification concerne ») — lu au démarrage du serveur. Une extension installée avant cette
  version continue de fonctionner ; téléchargez-la de nouveau pour qu'elle porte son propre nom.

- **Assistant IA : lancer une tâche.** Deux outils d'écriture de plus (`[ai] write_tools`, `ai:write`) : `job_form`
  montre une tâche comme sa page la montre à l'utilisateur — ce qu'elle fait, les paramètres qu'un utilisateur peut
  fixer avec leurs choix, ses préréglages, si elle est en cours, ses dernières exécutions avec leur état et leur
  erreur — et `job_run` est « Exécuter maintenant » : uniquement les paramètres déclarés et les préréglages de la
  tâche, sous `job:<id>`, par les fonctions des routes de la page de la tâche. Dans le chat c'est une carte ;
  l'exécution est mise en file et se poursuit en arrière-plan, et `job_form` la suit. Une tâche qui
  charge un fichier apporté par l'utilisateur le demande **sur la carte** : un sélecteur par étape de fichier, les
  types de fichier acceptés par le dépôt de la tâche ; Appliquer envoie le fichier par le dépôt de la tâche et
  lance la tâche avec — le fichier ne passe jamais par le modèle. Depuis Claude Desktop, le fichier vient
  du dossier de l'extension (ci-dessous).
- **Extension Claude Desktop : envoyer un fichier pour une tâche.** Un nouveau réglage facultatif de l'extension,
  **Dossier de fichiers** : avec lui l'extension propose deux outils qui s'exécutent sur l'ordinateur de
  l'utilisateur — `local_files` (les classeurs et fichiers délimités de ce dossier, et d'aucun autre) et
  `send_file` (l'un d'eux vers Liberty, pour une tâche) — et `job_run` nomme ensuite le fichier. Le fichier arrive
  dans la zone de dépôt de la tâche (`POST /mcp/files`, le jeton personnel ; les mêmes types de fichier et la
  même limite de taille que la page de la tâche ; `ai:write`, `job:<id>` et `[ai] mcp_changes`). Rien ne
  s'exécute au dépôt. Téléchargez de nouveau l'extension pour obtenir le réglage. Pas pour le connecteur par
  connexion, qui n'a pas accès au disque de l'utilisateur. Tracé comme les
  autres modifications (canal `apply`) ; en MCP, il faut `[ai] mcp_changes`.
- **Surveillance → Usage de l'IA, refondu.**
  - *Coût* : une estimation au prix public (USD) sur la période, par utilisateur, par jour et par modèle — un
    modèle sans prix connu est écarté et nommé. Les prix sont ceux de la version (`liberty/ai/catalog.py`).
  - *Canaux distincts des modèles* : un outil appelé en MCP et une carte appliquée apparaissaient comme des
    modèles nommés « mcp » et « apply ». « Par modèle » contient désormais ce qui a répondu aux questions du chat,
    et un nouveau panneau « Par canal » compte le chat, les clients externes et les cartes appliquées.
  - *Modifications faites par l'assistant* : un panneau les liste — qui, quand, par quel canal, quoi (l'écran,
    l'opération, la clé de l'enregistrement, la tâche) et la raison d'un refus.
  - *Dernière activité* : une série d'appels d'outils MCP d'un même utilisateur tient en une ligne (c'était une
    ligne par appel), ses appels s'affichant à l'ouverture ; un filtre par canal et « échecs seulement ».
  - Six panneaux sur deux colonnes, sans demi-ligne vide.
- **Données sensibles, corrections** (trouvées en rédigeant la documentation) :
  - les messages des actions qu'un écran exécute après une écriture (`on_insert`…) étaient remplis avec les
    valeurs sensibles ; ils sont masqués comme ceux d'une action ;
  - un champ sensible ne se filtre plus que sur une valeur entière donnée par l'utilisateur (égalité) : une plage
    ou un « contient » permettait de retrouver ses valeurs décompte après décompte — dans l'Explorateur et dans
    `sql_query` ; et toute mesure autre qu'un décompte est refusée (la moyenne aussi, que la note 7.0.81 omettait) ;
  - l'option **Sensible** d'une colonne d'écran a trois états dans l'éditeur (comme l'élément / oui / non) : la
    case à cocher écrivait « non » dès qu'on la décochait, sans retour possible — toute option facultative
    oui / non des éditeurs y gagne.
- Les outils d'écriture de l'assistant respectent `[ai] allowed_connectors` : un écran dont les données sont sur
  un autre connecteur n'existe pas pour eux.
- La page de consentement OAuth dit ce que le client pourra faire sur CE serveur — lire, et modifier seulement si
  `[ai] mcp_changes` l'autorise — et combien de temps l'accès dure.
- Le registre d'usage ne conserve plus le texte d'une note proposée par l'assistant.
- Le modèle par défaut d'une nouvelle installation est `claude-opus-5-5` (`claude-opus-4-8` auparavant) ; une
  installation qui fixe son modèle garde le sien.
- Assistant IA, `screen_write` : **une valeur choisie dans une liste renseigne ses colonnes associées**, comme dans
  le formulaire. Là où la liste d'une colonne d'écran renseigne d'autres champs à partir de la ligne choisie
  (`return_binds` — dans le Security Workbench, l'objet donne son code produit), l'écran les renseigne désormais
  quand l'assistant écrit la valeur ; une colonne associée fournie par l'appelant reste la sienne. `screen_form`
  les nomme (`also_fills`), la carte les affiche.
- **MCP : les modifications ont leur propre interrupteur — `[ai] mcp_changes`, désactivé par défaut.** Dans le chat
  Liberty une modification est une carte que l'utilisateur applique ; en MCP le seul garde-fou est l'approbation du
  client, que son utilisateur peut régler sur « toujours autoriser ». Un outil qui enregistre quelque chose — un
  enregistrement par son écran, une action, **et une analyse ou un tableau de bord sauvegardé de l'Explorateur** —
  n'est désormais proposé à un client externe, et exécuté pour lui, que si un administrateur l'autorise
  (Administrer → App → Assistant IA → « Autoriser les modifications en MCP » ; lu à chaque requête). **À la mise à
  jour :** un client Claude qui sauvegardait des analyses en MCP ne le peut plus tant que l'interrupteur est
  désactivé ; la lecture ne change pas.
- **Ce que l'assistant a modifié est tracé.** Une carte appliquée (un enregistrement écrit, une action lancée) est
  conservée dans le registre d'usage sous le canal `apply` : qui, l'écran, l'opération ou l'action, la clé de
  l'enregistrement, et la raison en cas de refus — jamais les valeurs. Avec les lignes MCP des mêmes outils (qui
  portent maintenant aussi la clé de l'enregistrement), Surveillance → Usage de l'IA liste toute modification faite
  par l'assistant. Sur un écran suivi, le paquet de modifications nomme ces entrées « AI assistant », comme il
  nomme celles d'une action.

## 7.0.81 — 2026-11-19

- **Assistant IA : les données sensibles ne sont pas envoyées.** Une installation décide de ce qui est sensible et
  Liberty le tient hors de portée de l'assistant — dans le chat comme en MCP.
  - *Ce qui est sensible* revient à l'installation (rien n'est marqué dans ce que livrent les applications) : une
    nouvelle option **Sensible** sur un élément du dictionnaire (Dictionnaire → une entrée → Règle) couvre toute
    colonne portant l'élément, sur chaque écran, requête et modèle de l'Explorateur — trouvée comme le périmètre
    de données trouve ses colonnes (l'élément de la colonne d'écran, le nom de la colonne, le nom sans le préfixe
    JDE). La même option sur une colonne d'écran couvre une colonne sans élément, ou en exempte une (`false`).
  - *L'interrupteur* est `[ai] hide_sensitive` (Administrer → App → Assistant IA), désactivé par défaut.
  - *Ce qui est alors écarté* : les colonnes marquées des lignes lues par `sql_query` (la réponse nomme les
    colonnes masquées, pour que l'assistant puisse le dire) ; les valeurs d'un enregistrement sur la carte d'une
    écriture ou d'une action (« avant », l'enregistrement d'une suppression, la confirmation et les messages de
    l'écran — la valeur que l'utilisateur a demandé d'écrire est la sienne et s'affiche) ; le contexte de page
    (la ligne cliquée, la liste des colonnes, un filtre saisi sur une telle colonne) ; dans l'Explorateur, un
    champ marqué ne peut être ni un en-tête de ligne ni lu par une mesure autre qu'un décompte — il peut être
    compté, et filtré sur une valeur donnée par l'utilisateur. Les écrans eux-mêmes ne changent pas.
  - *Ce que cela ne couvre pas* — à savoir avant de s'y fier : ce qu'un utilisateur saisit dans une question ou
    joint ; une colonne de texte libre non marquée ; ce que renvoient un point d'accès API (`api_call`), un outil
    propre à une application ou les outils d'exploitation, dont Liberty ne connaît pas les colonnes.

- Assistant IA, écriture par un écran :
  - **Un enregistrement ouvert dans son formulaire n'est pas modifié.** Le formulaire verrouille l'enregistrement
    qu'il affiche ; `screen_write` refuse désormais la modification ou la suppression d'un enregistrement que
    quelqu'un a ouvert — l'autre utilisateur, nommé, ou le formulaire ouvert de l'utilisateur lui-même, qui
    réécrirait sinon ses anciennes valeurs à sa prochaine sauvegarde. Dit sur la carte avant toute proposition.
  - **Une valeur située loin dans une longue liste n'est plus refusée.** Une liste de recherche plus longue que
    la limite de lignes de son pool revient coupée, comme dans le formulaire ; quand la valeur contrôlée n'est
    pas dans la partie lue, la liste est relue restreinte à cette valeur avant de décider.
- `[ai] max_iterations` — les tours d'outils qu'une question peut utiliser — vaut **15** par défaut (8 auparavant) :
  une procédure qui écrit plusieurs enregistrements par ses écrans demande plus de tours qu'une question (créer un
  utilisateur JDE : une dizaine d'appels d'outils). Une installation dont `app.toml` fixe la valeur garde la sienne.
- Le filtre de colonne d'un écran et le périmètre de données d'un rôle sur une colonne de résultat nommée comme un
  mot réservé (`ULUSER "USER"` sur JD Edwards) échouaient comme le filtre de l'assistant en 7.0.80 (ORA-01747, et
  ORA-01745 pour le paramètre) : la colonne est maintenant désignée entre guillemets dans les trois enveloppes,
  avec un nom de paramètre propre dans le filtre d'écran.
- Supervision → Rétention : le même écrasement que celui de l'Usage de l'IA dans une fenêtre basse — la liste
  garde sa hauteur et la page défile.

## 7.0.80 — 2026-11-19

- **Assistant IA : écrire par un écran.** Trois nouveaux outils, désactivés par défaut : `screen_form` (ce qu'un
  écran laisse ajouter, modifier, supprimer et lancer : la clé, chaque colonne avec son libellé, si elle est
  obligatoire ou verrouillée, sa valeur par défaut et les valeurs qu'elle accepte, et les actions de l'écran),
  `screen_write` (ajouter, modifier ou supprimer UN enregistrement) et `screen_action` (lancer UNE action de
  l'écran — « Valider » / « Clôturer » du menu de ligne, un bouton de la barre d'outils ou d'un onglet du
  formulaire). Rien n'est fait par un autre chemin que celui de l'écran :
  - un enregistrement est d'abord lu par la requête de lecture de l'écran, les règles du formulaire sont
    appliquées — une colonne obligatoire vide, une valeur hors de sa liste, une colonne ou un enregistrement que
    l'écran tient en lecture seule (`editable_when`), une colonne qui ne s'applique pas à l'enregistrement sont
    refusées avec la raison — et chaque instruction passe par la fonction de la sauvegarde de l'écran : règles du
    dictionnaire, séquences, tables liées 1:1, table d'audit, périmètre de données, paquet de modifications,
    règles de notification ;
  - les actions de sauvegarde de l'écran (`on_insert` / `on_update` / `on_delete`, `on_save` du formulaire) et
    ses actions s'exécutent sur le serveur comme le navigateur les exécute — `run_query`, `call_api`,
    `call_plugin`, actions partagées (`call_action`), `chain` / `if` / `loop` / `return`, paramètres pris dans
    l'enregistrement, une étape précédente ou une valeur intégrée (`#LOGIN_USER#`…) — chaque étape sous sa propre
    permission et avec la capture du paquet de modifications de l'écran. Un `confirm` revient à l'utilisateur
    (ci-dessous) et son message est affiché ; le message d'un `notify` est renvoyé ; une valeur demandée par une
    action est fournie d'avance (absente et obligatoire : rien ne s'exécute) ; ce que seul un navigateur fait
    (`navigate`, `open_url`, `refresh`) est signalé, et une action qui ne fait que cela n'est pas proposée.
  Trois verrous, tous nécessaires : `[ai] write_tools` (Administrer → App → Assistant IA), la nouvelle permission
  `ai:write` (Accès → un rôle → Au-delà des menus → IA) et la permission de chaque requête, point d'accès ou plugin exécuté,
  comme dans l'écran. **Qui confirme :** dans le chat Liberty les outils ne font que proposer — l'utilisateur
  reçoit une carte (l'enregistrement avant et après, ou l'action avec la confirmation de l'écran et ce qu'elle
  exécute) et rien ne se passe avant Appliquer ; en MCP le client (Claude) demande à son utilisateur avant
  d'appeler un outil qui n'est pas en lecture seule. Comme dans l'écran, une sauvegarde, ses tables liées et ses
  actions sont des instructions séparées : une action ou une étape qui échoue laisse ce qui a été fait avant, et
  la réponse le dit. Une valeur issue d'une requête (une liste de recherche) est contrôlée par rapport à
  sa liste comme le fait l'import de la grille — la règle et les paramètres qui s'appliquent à cet enregistrement,
  une liste illisible étant refusée. Pas encore : un formulaire qui exécute des actions à son ouverture
  (`on_load`) est refusé avec cette raison.

- **MCP : une extension Claude Desktop, pour un serveur qui n'est pas sur Internet.** Un connecteur personnalisé
  est appelé depuis les serveurs d'Anthropic et exige donc une adresse publique. L'onglet Jetons d'accès du profil
  propose désormais le bouton **Extension Claude Desktop** (`liberty.mcpb`) : ouverte avec Claude Desktop, elle exécute sur
  l'ordinateur de l'utilisateur une petite passerelle qui relaie les messages de Claude vers `/mcp` avec le jeton
  d'accès personnel de l'utilisateur — le serveur n'a qu'à être joignable depuis cet ordinateur (une adresse
  interne, un VPN). Le fichier est construit au téléchargement avec l'adresse du serveur renseignée et ne contient
  aucun secret : l'utilisateur y colle un jeton créé dans le même onglet ; une option accepte un certificat
  auto-signé ou d'entreprise. Rien à installer (Claude Desktop fournit Node). Les mêmes droits que tout accès
  MCP : `mcp:use`, les permissions et le périmètre de données de l'utilisateur. `GET /auth/mcp/extension`.

- Un filtre donné par colonne ou par élément du dictionnaire (les `filters` de `sql_query` de l'assistant, le filtre
  d'un tableau de bord) échouait sur une colonne de résultat nommée comme un mot réservé — `ULUSER "USER"` sur
  JD Edwards : ORA-01747. La colonne est maintenant désignée entre guillemets, sous le nom que la base lui donne.
- `screen_write` sur un écran qui est un onglet du formulaire d'un autre enregistrement (Relations de rôles sous un
  utilisateur) : la colonne qui le relie au parent — masquée et verrouillée dans le formulaire, qui la renseigne —
  doit maintenant être fournie à l'insertion et est acceptée ; auparavant, la fournir était refusé et l'omettre
  écrivait un enregistrement n'appartenant à personne. Un enregistrement dont la clé contient une partie vide peut
  être désigné (la partie vide donnée comme chaîne vide), donc modifié ou supprimé.
- Assistant IA : une question qui avait utilisé tous ses tours d'outils (`[ai] max_iterations`, 8 par défaut) se
  terminait sur « pas de réponse » et une erreur — rien pour ce que les tours avaient coûté. Elle se termine
  maintenant sur une réponse de clôture, demandée outils désactivés : ce qui a été fait, ce qui a été trouvé, ce
  qui reste, et que l'utilisateur peut demander de continuer. Surveillance → Usage de l'IA affiche un tel tour comme
  « Limite de tours ». Une procédure qui écrit plusieurs enregistrements demande plus de tours qu'une question :
  augmentez `max_iterations` (Administrer → App → Assistant IA) là où les outils d'écriture sont activés.
 dans une fenêtre trop basse pour toute la page, les panneaux étaient écrasés au lieu
  que la page défile — la liste des outils coupée, les dernières questions invisibles sauf à dézoomer le navigateur.

## 7.0.79 — 2026-11-19

- **Point d'accès MCP : se connecter au lieu de coller un jeton (OAuth).** Un client à qui l'on ne donne que
  l'adresse de `/mcp` — un connecteur personnalisé dans Claude Desktop ou claude.ai — trouve désormais la connexion
  par lui-même : le navigateur ouvre Liberty, l'utilisateur se connecte comme d'habitude (mot de passe ou SSO) et
  autorise le client sur une page de consentement ; rien à créer, rien à coller. Liberty est son propre serveur
  d'autorisation (OAuth 2.1 tel que la spécification MCP le décrit : découverte sous `/.well-known/`,
  enregistrement du client, code d'autorisation avec PKCE, jeton de rafraîchissement tournant), et rien n'est
  stocké : l'enregistrement du client, le code et le jeton de rafraîchissement sont des jetons signés. L'accès est
  le même qu'avec un jeton collé — les permissions et le périmètre de données de l'utilisateur, relus à chaque
  appel, permission `mcp:use` — et prend fin de la même façon : changement de mot de passe, compte désactivé ou
  perte de `mcp:use`. Le consentement dure `[ai] mcp_token_max_days` ; ensuite l'utilisateur autorise de nouveau
  le client. Un jeton d'accès personnel collé en en-tête de requête fonctionne toujours (Claude Code, scripts).
  Derrière un proxy, renseignez `[app] public_url` (Administrer → App) avec l'adresse à laquelle les utilisateurs
  joignent le serveur : les adresses publiées par la connexion sont construites dessus.

## 7.0.78 — 2026-11-18

- **Rapport d'usage de la licence.** Une installation sous licence signale une fois par jour à l'outil de gestion des
  licences quel environnement elle est, sa version — celle du framework (`liberty-next`) et, quand les applications
  sont installées, la leur (`liberty-apps`), un rapport chacune — et son nombre de comptes actifs — rien d'autre : aucun chiffre des
  applications, aucun nom, aucune donnée. Il est envoyé par une tâche nomaflow livrée par le framework,
  `liberty-usage-report`, volontairement visible : elle figure sous Nomaflow → Tâches, chaque exécution montre ce
  qui a été envoyé et la réponse de l'outil, un envoi refusé ou impossible est l'erreur de l'exécution, et elle se
  lance, se met en pause ou se replanifie comme toute tâche (sa première modification l'écrit dans `jobs.toml`). La
  tâche n'existe que là où il y a une clé de licence ; `[license] usage_url` (Administrer → App → Licence) est
  l'adresse du rapport — vide, rien n'est envoyé.
- `[app] environment` (Administrer → App) : ce qu'est l'installation — « production », « test »… — nommé dans ce
  rapport.
- nomaflow : le framework peut livrer ses propres tâches (`liberty.jobs.builtin`), ajoutées à la liste sauf si
  `jobs.toml` contient une tâche du même identifiant.
- Assistant IA : **joindre un fichier à une question**. Un trombone dans le chat permet de donner à l'assistant un
  fichier à lire : une image (une capture d'écran d'erreur), un PDF (une procédure), un classeur ou un CSV (une
  liste à comparer avec ce que contient l'installation), un fichier texte ou un journal. Une image et un PDF sont
  transmis tels quels au modèle ; un classeur, un CSV et un fichier texte le sont sous forme de texte — la première
  feuille, le séparateur détecté, coupés au-delà d'une taille avec une marque sur le fichier. L'assistant ne fait
  que le lire : rien n'est chargé dans une table, rien n'est conservé. Le fichier reste avec la conversation tant
  qu'elle est ouverte ; la conversation enregistrée n'en garde que le nom, et une question reprise indique que son
  fichier n'est plus là. `[ai] attachment_max_mb` (10 ; Administrer → App → Assistant IA ; 0 = aucune pièce jointe).
  Un fichier compte en jetons d'entrée à chaque question de sa conversation.
- Un serveur lancé depuis une copie du dépôt des applications (développement) connaît désormais la version des
  applications — d'après le `pyproject.toml` du dépôt — alors qu'il ne la connaissait que pour un wheel installé ou
  un bundle matérialisé : le rapport d'usage, le journal des mises à niveau et la page des notes de version la
  nomment.
- **Point d'accès MCP : `/mcp` était inaccessible sur un vrai serveur.** Le point d'accès ne répondait qu'à `/mcp/` ;
  `/mcp` sans barre finale — l'adresse donnée à un client — tombait sur la page de l'application, qui refuse un POST
  (405) : la vérification du connecteur de Claude indiquait « Connecting to the server — 405 ». Le défaut est passé
  inaperçu parce que les tests tournaient sans la page et suivaient la redirection qui existe alors. Le point d'accès
  est désormais routé sur `/mcp` lui-même, avant la page. En 7.0.77, donner au connecteur `https://<serveur>/mcp/`
  avec la barre finale. Les adresses de découverte OAuth (`/.well-known/oauth-protected-resource`,
  `oauth-authorization-server`) répondent maintenant un simple 404 au lieu de la page.

## 7.0.77 — 2026-11-18

- **Point d'accès MCP** (`/mcp`) — les outils de l'assistant depuis un client externe : Claude Desktop, claude.ai ou
  Claude Code atteignent une installation Liberty par un connecteur personnalisé et agissent en tant qu'utilisateur
  Liberty. Rien des outils n'est réécrit : le client se voit proposer ce que l'utilisateur a dans le chat, un appel
  passe par les mêmes contrôles de permission et le même périmètre de données, et chaque appel figure dans
  l'enregistrement d'usage (Surveillance → Usage de l'IA) sous le canal `mcp`. Les outils dont le résultat est une
  carte du chat Liberty (les propositions de génération) ne sont pas proposés ; un outil qui enregistre quelque
  chose le signale au client. Désactivé par défaut : `[ai] mcp_enabled` (Paramètres → App → IA, appliqué sans
  redémarrage). Nécessite le nouvel extra `mcp` — le paquet officiel `mcp` — inclus dans `liberty-next[all]` et
  dans l'image.
- **Jetons d'accès personnels** — ce qu'un utilisateur donne au connecteur à la place d'une connexion. Un
  utilisateur détenant la nouvelle permission `mcp:use` en crée un dans son profil (le nouvel onglet **Jetons
  d'accès**, ou `POST /auth/tokens`) : il n'est affiché qu'une fois et n'est pas conservé, valable 90 jours par
  défaut et `[ai] mcp_token_max_days` (366) au plus. Le compte est relu à chaque appel : le jeton porte les droits
  de l'utilisateur à l'instant, jamais plus ; il prend fin à son expiration, quand le compte est désactivé ou perd
  `mcp:use`, et quand le mot de passe du compte change — c'est ainsi qu'on met fin à un jeton qui a fuité (il n'y a
  pas de liste de jetons à révoquer un par un). Un jeton personnel n'ouvre que le point d'accès MCP ; le reste de
  l'API le refuse. Pour connecter Claude : un connecteur personnalisé sur `https://<serveur>/mcp` avec l'en-tête de
  requête `Authorization: Bearer <jeton>`.
- Assistant IA : `sql_query` exécute une requête comme le fait son écran. Depuis sa première version l'outil appelait
  le connecteur directement — un « simple exécuteur SQL », selon la documentation même de la route — et tout ce
  qu'apporte un écran manquait : les libellés et formats des colonnes, les filtres de l'écran, les filtres par
  dictionnaire, l'environnement de la page. Il passe désormais par la même fonction que la route des écrans
  (`web/connectors.select_events`, extraite de la route en flux, qui l'utilise aussi). L'outil accepte `filters` —
  `{colonne: valeur}`, transmis comme les liaisons de dictionnaire du framework (ce qu'envoie un filtre de tableau de
  bord) : le connecteur trouve la colonne, type et nettoie la valeur, avec ou sans écran ; une plage s'écrit
  `<colonne>_from` / `<colonne>_to`. Un filtre qui ne correspond à aucune colonne est refusé au lieu d'être ignoré.
  Sur une installation JD Edwards, « les types UDC du système 01 » ou « l'élément de données AN8 » devient un seul
  appel, au lieu de lire la table 200 lignes à la fois.
- `describe_instance` et `search_catalog` indiquent le connecteur d'un écran quand ce n'est pas celui de
  l'application (l'assistant appelait `nomajde.f0004_get` pour une requête située sur `jdedwards`).
- Assistant IA : les applications livrent des outils. `ai.toml` accepte `[ai.<app>.tools.<id>]` — une fonction des
  plugins de l'application (`callable = "module:fonction"`, un des points d'entrée `j_`), la description d'après
  laquelle le modèle le choisit, et la permission requise pour l'utilisateur (`plugin:<callable>` sauf si une autre
  est indiquée). Le modèle le voit sous le nom `<app>_<id>`, avec pour arguments les paramètres de la fonction (types
  d'après les annotations, descriptions d'après la docstring) ; `connectors`, `settings` et `ctx` sont fournis par le
  serveur, jamais par le modèle. Un outil n'est proposé qu'à un utilisateur détenant sa permission, seulement quand
  son application est chargée (un connecteur sous licence sans sa clé ne livre rien) et ouverte à l'assistant ; une
  fonction qui n'est pas un point d'entrée de plugin, ou qui ne peut pas être importée, est écartée avec un
  avertissement. En lecture seule par contrat. Édités sous **IA → Outils**, appliqués au rechargement sans
  redémarrage ; `[ai] app_tools = false` (Paramètres → App → IA) les désactive tous.
- Paramètres → IA : un enregistrement ne supprime plus ce que la page n'affiche pas — les questions de démarrage par
  langue (`l.fr`) étaient perdues à chaque enregistrement, les groupes de questions d'un écran quand ses questions
  étaient modifiées.
- La vue Synthèse de la grille fonctionne sur une requête Oracle : sa colonne de décompte s'appelle `_count`, nom
  qu'Oracle refuse sans guillemets (ORA-00911) — toute synthèse sur un pool Oracle échouait. Le nom est désormais
  entre guillemets dans le SQL généré.
- Assistant IA : les conversations sont conservées pour l'utilisateur. Le volet de chat enregistre chaque
  conversation sur le serveur (le stockage des vues enregistrées par utilisateur, un nouveau type `chat`) et non plus
  seulement dans le navigateur : un bouton **historique** les liste — reprendre, supprimer — et elles suivent
  l'utilisateur d'un appareil à l'autre. Ce qui est conservé est ce que l'utilisateur a vu (les questions et les
  réponses) ; une conversation reprise relit ses données. 30 conversations par utilisateur au plus, les plus
  anciennes supprimées.
- Assistant IA : les **notes**. Demandez à l'assistant de retenir quelque chose pour les conversations suivantes —
  une convention, une préférence, la définition d'un chiffre — et il propose une note sur une carte ; rien n'est
  enregistré tant que l'utilisateur ne l'accepte pas. Les notes de l'utilisateur pour l'application de la page (et
  celles valables pour toutes les applications) accompagnent ensuite ses questions. Elles sont privées,
  listées et supprimées dans le même panneau que les conversations (type `ai_note` du stockage des vues
  enregistrées).

## 7.0.76 — 2026-11-18

- Menus : une application est listée pour ce que l'utilisateur peut y ouvrir. **Un changement de comportement pour
  les utilisateurs restreints** : une entrée de tableau de bord était affichée dès que le tableau de bord existait et
  la page Explorateur de données à tout le monde ; un utilisateur ayant les permissions d'une seule application
  voyait donc toutes les applications dans le sélecteur — chacune avec un tableau de bord vide et un Explorateur sans
  modèle. Désormais une entrée de tableau de bord est affichée à qui peut lire au moins un de ses widgets de données
  (ou détient `dashboard:<id>` ou le `menu:<app>:<id>` de l'entrée ; un tableau de bord fait seulement de textes et de
  liens reste affiché), la page Explorateur de données à qui peut utiliser un modèle de cette application
  (`explorer:<app>:<modèle>`), et les liens sans permission (une page ouverte, une adresse externe) ne suffisent plus
  à lister une application. Rien ne change pour un super-utilisateur ou un rôle détenant `*`. Pour vérifier un rôle :
  Paramètres → Accès montre ce qu'il voit — l'éditeur de rôle suit la même règle (`GET /admin/access/menu-rules`).
  L'assistant IA, qui nomme les applications selon cette règle, suit.
- Image : les correctifs de sécurité du système de base sont appliqués à la construction (`apt-get upgrade` : apt,
  zlib) et `pyasn1` passe en 0.6.5 — l'analyse de l'image à la publication refusait 7 alertes HIGH, toutes corrigées
  en amont.
- Assistant IA : un enregistrement d'audit et d'usage. Chaque question posée à l'assistant est conservée dans
  `ly2_ai_call` (créée au démarrage, sur le pool d'authentification) : qui a demandé, depuis la page de quelle
  application, le modèle, les jetons (entrée, sortie, lus depuis / écrits dans le cache), la durée, l'issue, et
  chaque appel d'outil — son nom, sur quoi il a été appelé (le connecteur et la requête, le modèle, les mots-clés),
  s'il a échoué et pourquoi, sa taille et sa durée. **Jamais la question, la réponse ni une ligne renvoyée par un
  outil.** Une question abandonnée en cours de route est enregistrée avec ce qui s'était passé ; un stockage en
  panne n'interrompt jamais une conversation.
- **Surveillance → Usage de l'IA** (super-utilisateurs) : la période en chiffres — questions, utilisateurs, jetons
  avec la part lue depuis le cache, appels d'outils et échecs, durée moyenne — par utilisateur, jour, modèle et
  outil, et les dernières questions avec leurs appels d'outils (un clic sur un utilisateur les restreint).
  `GET /admin/ai-usage`.
- `[ai] usage_retention_days` (90 ; Paramètres → App → IA ; 0 conserve tout) : les questions plus anciennes sont
  supprimées avec la purge des notifications, et le stockage figure dans **Supervision → Rétention** (taille,
  aperçu, purge).
- Assistant IA : les outils répondent pour l'utilisateur connecté. **Un changement de comportement** : jusqu'ici
  `ai:chat` seul permettait à l'assistant d'exécuter toutes les requêtes de lecture des connecteurs qui lui sont
  ouverts (le périmètre de données s'appliquait, pas les permissions). Chaque outil vérifie maintenant ce que
  vérifient les écrans : `sql_query` demande `sql:<connecteur>:<requête>`, `api_call` `api:<connecteur>:<endpoint>`,
  les outils de l'Explorateur de données `explorer:<app>:<modèle>` ; `list_connectors`, `describe_instance`,
  `search_catalog` et les playbooks ne montrent que ce que l'utilisateur peut ouvrir (un écran par sa requête de
  lecture, une entrée de menu selon la règle du menu, une tâche par `job:<id>`) — une application sans rien pour
  l'utilisateur n'est pas nommée du tout, et un refus est le même pour une requête qui existe et une qui n'existe
  pas. Les outils d'exploitation et de génération sont proposés aux super-utilisateurs uniquement. Un utilisateur
  qui avait `ai:chat` sans les permissions `sql:` / `explorer:` d'une application obtient moins de réponses de
  l'assistant : les accorder, comme pour les écrans.
- Assistant IA : trouver une requête ou un écran ne demande plus de lire tout le catalogue. `list_connectors` ne liste
  que les requêtes de lecture (une requête d'écriture était listée alors que l'assistant ne peut jamais l'exécuter)
  et, sur une installation trop grande pour être listée en une fois, renvoie une vue d'ensemble — le modèle précise
  ensuite avec `connector` (la liste d'un connecteur) ou `search` (mots-clés sur le nom, le libellé ou la
  description). `describe_instance` accepte une `app`, et un nouvel outil, `search_catalog`, trouve un écran, une
  entrée de menu, un tableau de bord, un modèle de l'explorateur, une tâche ou un playbook par mot-clé. Sur une grande
  installation la liste complète était coupée à la limite des résultats d'outil — le modèle ne voyait pas une partie
  des requêtes — et repayée à chaque tour de la conversation.
- Assistant IA : la conversation est mise en cache à mesure qu'elle grandit. Dans une même question, chaque tour
  d'outils renvoyait les tours précédents — surtout leurs résultats d'outils — au plein tarif d'entrée ; ils sont
  maintenant lus depuis le cache de prompt (cache automatique sur la requête, en plus du point de cache du prompt
  système). L'outil d'évaluation affiche la part de l'entrée lue depuis le cache.
- Assistant IA : un jeu d'évaluation. `python -m liberty.ai.evals <fichiers de cas>` joue des questions sur un serveur
  en marche (le compte de `LIBERTY_E2E_USER` / `LIBERTY_E2E_PASSWORD` : les permissions et le périmètre de données
  font donc partie de ce qui est mesuré) et vérifie chaque réponse : les outils appelés, les textes et les chiffres
  qu'elle doit contenir, aucun appel d'outil en échec. Il donne le taux de réussite, les appels d'outils, les jetons
  et la durée ; `--out` conserve les résultats complets comme référence. À lancer à la main avant et après un
  changement du prompt, du modèle ou d'un outil ; les applications livrent leurs cas.
- Assistant IA : enregistrer Paramètres → App ne retire plus une partie des outils de l'assistant. L'enregistrement
  reconstruisait l'assistant sans les outils d'instance — `describe_instance`, les playbooks, l'Explorateur de
  données et les outils d'exploitation — si bien que jusqu'au redémarrage ou rechargement suivant il ne répondait
  qu'à partir des requêtes des connecteurs.
- Assistant IA : `sql_query` a son propre plafond de lignes — `[ai] sql_max_rows` (200 par défaut, Paramètres → App →
  IA). Il héritait du `max_rows` du pool, dimensionné pour la grille (lignes en flux) : sur un pool à 500 000, une
  question pouvait lire une table entière dans un seul résultat d'outil. Au-delà du plafond le résultat indique
  `truncated` et demande au modèle de ne pas compter ni additionner les lignes reçues ; le modèle peut en demander
  moins avec `limit`.
- Pools : `[connectors] pools_path` dans app.toml désigne un fichier local dont les `[pools.*]` remplacent les pools
  de même nom de connectors.toml (un pool qu'il ne cite pas vient toujours de là) — pour le serveur, les CLI
  (`liberty-ops` sauvegarde / doctor, `liberty-admin`, `liberty-connectors`) et le rechargement à chaud ; l'éditeur
  des pools enregistre alors dans ce fichier. Pour un poste de développement dont connectors.toml est celui du dépôt
  des applications : ses propres hôtes et mots de passe restent hors du fichier partagé, les connecteurs qu'on y
  modifie sont toujours commités. Non renseigné (par défaut), rien ne change.
- Les éditeurs des pools et des connecteurs n'aplatissent plus les références `${VAR}` à l'enregistrement : l'éditeur
  lit les valeurs résolues, l'enregistrement réécrivait donc la valeur résolue — `${POSTGRES_HOST:-pg}` devenait
  l'hôte vers lequel il pointait, et un changement ultérieur de l'environnement restait sans effet sur ce pool. Une
  valeur laissée telle quelle est désormais réécrite sous forme de référence ; une valeur modifiée est écrite telle
  que saisie. Un mot de passe donné par une référence `${VAR}` n'est plus chiffré en une copie `ENC:` de la référence.
- `./start.sh` charge `config/app-dev.toml` quand il existe et que `LIBERTY_APP_CONFIG` n'est pas défini — les
  réglages propres d'un développeur (ignorés par git), le `config/app.toml` commité reste tel que livré.

## 7.0.70 — 2026-10-30

- nomaflow : le détail d'une exécution a un bouton **Retour** — vers la page d'où elle a été ouverte (la liste des
  tâches par le statut d'une tâche, la liste des exécutions, une exécution parente) ; ouverte depuis un lien, vers la
  liste des tâches.

## 7.0.69 — 2026-10-30

- Comptes : un nouveau mot de passe à la première connexion. Un compte créé — ou réinitialisé par un administrateur —
  avec un mot de passe temporaire (« Demander un nouveau mot de passe à la prochaine connexion » dans Paramètres →
  Accès, coché par défaut pour un nouvel utilisateur ; `liberty-admin create-user / set-password --must-change`)
  s'ouvre sur une page « Choisissez votre mot de passe » ; tant que ce n'est pas fait, le serveur refuse tout appel
  sauf le changement lui-même (403 « password change required » — imposé par le serveur, pas seulement par la page).
  Comptes locaux uniquement : un compte SSO n'est jamais concerné.
- Connexion : « Mot de passe oublié ? ». L'utilisateur indique son identifiant et l'e-mail du compte ; quand les deux
  correspondent à un compte local, un lien est envoyé — valable une heure, et une seule fois (il est lié au mot de
  passe pour lequel il a été émis). La réponse est la même qu'ils correspondent ou non : elle ne révèle jamais qui a
  un compte ; les demandes sont limitées par adresse. Proposé quand le SMTP est configuré (`[notify]`) ; le lien pointe
  vers la nouvelle `[app] public_url` (Paramètres → App), sinon l'adresse du navigateur. Nouvelles routes publiques :
  `POST /auth/forgot-password`, `POST /auth/reset-password`.
- nomaflow : une étape peut être désactivée pour une installation — `disabled_steps = ["SECURITY_LDAP"]` sur le job,
  une case à cocher dans l'éditeur d'étape. Elle ne s'exécute jamais, même quand une exécution ou un préréglage
  sélectionne toutes les étapes (la boîte d'exécution la montre désactivée et non sélectionnable) ; elle est
  enregistrée comme ignorée, pas en échec. Un réglage de l'exploitant comme la planification : l'installateur des
  applications le conserve, et le job ne paraît pas modifié (les corrections de l'éditeur sur ses autres étapes
  arrivent donc toujours) — contrairement au `enabled` d'une étape, valeur par défaut de l'éditeur qu'une exécution
  peut remplacer.
- `./liberty apps` et `./liberty upgrade --apps` redémarrent liberty-next après l'installation du wheel quand la pile
  tournait déjà : `docker compose up -d` ne recrée qu'un conteneur dont la configuration a changé, donc lors d'une mise
  à jour le serveur gardait les plugins et la configuration chargés avant, jusqu'à un `./liberty restart` manuel. Une
  première installation (le conteneur créé par ce même `up -d`) n'est pas redémarrée deux fois.

## 7.0.68 — 2026-10-29

- nomaflow : une étape peut laisser le job continuer quand elle échoue — `continue_on_failure = true` (sur l'étape, ou
  pour toutes les étapes avec les `step_defaults` du job ; une case à cocher dans l'éditeur d'étape). Après ses
  tentatives, l'étape est enregistrée en échec, les suivantes s'exécutent et l'exécution finit en échec avec « N
  step(s) failed, the others ran — <étape> : <erreur> ». Pour un job qui enchaîne des traitements indépendants, où
  l'échec d'une partie ne doit pas sauter les autres. Par défaut, rien ne change : une étape en échec arrête le job.
- Connecteurs API : un serveur par environnement. Avec plusieurs environnements JDE sur un même connecteur SQL
  (`pools`), ses appels AIS vont à l'AIS de l'environnement à l'écran — `pool` nomme l'environnement du serveur du
  connecteur, `targets` donne les serveurs des autres environnements (URL, utilisateur, mot de passe, jeton,
  vérification TLS ; les points d'accès restent partagés). Personne ne choisit le serveur : il suit l'environnement
  choisi dans le menu, le `pool` d'une étape de job (tout type d'étape désormais), et couvre les actions (`call_api`),
  les plugins (`nomajde/ais.py`), `/api/http` et l'assistant. Un appel pour un environnement de ces connecteurs sans
  serveur est refusé (« no target for pool … »), jamais envoyé au serveur par défaut (souvent la production).
  Paramètres → Connecteurs → Environnements les édite (mots de passe chiffrés à l'enregistrement) et signale un
  environnement sans serveur ; Paramètres → Intégrité aussi. L'installateur des applications conserve le `pool` /
  les `targets` d'une installation comme ses pools, et apporte quand même les nouveaux points d'accès de l'éditeur.
- Actions : une étape `confirm` pose de nouveau sa question. Aucun écran ne transmettait la boîte de dialogue au
  moteur d'actions (le formulaire, le menu de ligne, la barre d'outils, l'édition en masse) : un `confirm` ne faisait
  qu'ajouter un avertissement et la chaîne continuait sans demander — dont « Close this period? » / « Reopen this
  period? » de nomademo.
- Actions : le message d'un `confirm` ou d'un `notify` se remplit comme un modèle `open_url` — `{CHAMP}`,
  `{INPUT.x}`, un chemin de la chaîne, les variables intégrées — et `#POOL#` nomme l'environnement de l'écran : le
  pool choisi dans le menu quand le connecteur de l'écran l'a, sinon le pool de ce connecteur — celui où vont ses
  appels AIS. « Reset the password of {ULUSER} on #POOL#? ». (`/api/connectors` donne le nom du pool par défaut de
  chaque connecteur SQL avec ses noms de pools.)

## 7.0.67 — 2026-10-29

- Formulaire : un champ date-heure (format `datetime` / `timestamp`) se modifie avec son jour et son heure — le
  sélecteur de date et un champ heure (`hh:mm` ou `hh:mm:ss`) — au lieu du seul jour, qui l'enregistrait à minuit
  (`common/DateTimePicker`). En lecture seule, une date-heure avec fuseau s'affiche à l'heure locale, comme la grille
  (elle affichait la valeur UTC brute).
- Écritures : un horodatage `SYSDATE` prend le format date / heure du dictionnaire quand l'écran affiche la colonne
  dans un autre format — une heure JDE affichée en simple `number` (F00950.FSUPMT, F95921.RLUPMT, F0004.DTUPMJ…)
  recevait la date-heure brute, refusée par Oracle pour la colonne NUMBER (ORA-00932). Tout import « Mettre à jour ou
  insérer » de F00950 échouait ainsi (sa vérification par mise à jour n'envoie pas l'heure) ; depuis juin.
- Import : le contrôle des règles charge les listes de valeurs dont chaque ligne a besoin — une liste que la grille
  n'avait pas encore chargée (un import ouvert dès l'ouverture de l'écran) ou une liste restreinte par les valeurs de
  la ligne — au lieu de l'ignorer et de déclarer la ligne valide (`ZZ` passait comme langue). Une liste impossible à
  charger met la ligne en erreur.
- Formulaire : quand une action du cycle de vie échoue après l'écriture de la ligne (un appel d'API d'`on_insert` en
  erreur, avec `stop_on_error`), le formulaire reste ouvert avec l'erreur — le rafraîchissement le fermait et
  masquait le message. `onSaved` ne fait plus que rafraîchir ; le formulaire se ferme lui-même après un
  enregistrement ou une suppression réussis. Une action d'onglet demandant un rafraîchissement ferme toujours le
  formulaire (laissé ouvert, il garderait les valeurs chargées et un Enregistrer suivant les réécrirait).
- Formulaire : une action d'onglet dont la saisie est annulée n'affiche plus rien — elle affichait « <action> · OK ».
- Édition en masse : « Cloner la ligne » et le collage gardent toutes les valeurs, clés comprises, sauf une colonne
  remplie par une `SEQUENCE` (ses `rules`) : laissée vide, l'insertion en prend une nouvelle — et les lignes liées
  1:1 suivent la nouvelle clé (l'enregistrement cherchait la clé parente dans la mauvaise casse et l'insertion liée
  échouait sur sa clé étrangère). Les colonnes portent `sequence`.
- Éditeur SQL (et toute lecture) : une valeur binaire (un BLOB Oracle) revient en `(binary, N bytes)` — l'encodeur
  JSON échouait et la requête finissait en HTTP 500 brut ; l'éditeur SQL répond à toute autre erreur du pilote par
  son message.
- Un lien ouvert dans un navigateur neuf (aucune application mémorisée) ouvre sa page, son application
  sélectionnée — il arrivait sur l'accueil de la première application.
- Formats `url` / `email` / `color` (entrée du dictionnaire ou colonne) : la grille affiche un lien ouvert dans un
  nouvel onglet, un `mailto:`, une pastille de couleur à côté du code ; la saisie du formulaire reçoit un bouton
  d'ouverture, ou une pastille ouvrant le sélecteur de couleur du système. Seules les valeurs sûres : un lien est
  `http(s)` uniquement (`www.…` reçoit `https://`), un e-mail doit ressembler à une adresse, une couleur est un code
  hexadécimal ou `rgb()` — le reste reste du texte. Tri, filtre et export gardent la valeur brute.
- Dates : un sélecteur de date aux couleurs du thème (`common/DatePicker`) remplace le calendrier du navigateur —
  blanc, et différent d'un navigateur à l'autre — dans le formulaire, l'édition en masse de la grille, les filtres de
  colonne de la grille et les filtres du Data Explorer : un champ où une date peut encore être saisie (un jour
  impossible revient à la valeur) et le calendrier du sélecteur de période, avec Aujourd'hui et Effacer. La valeur
  reste une date ISO. Le calendrier est partagé (`common/MonthGrid`) ; son titre ouvre un choix du mois et de l'année
  (les 12 mois, l'année par flèches ou 12 à la fois) — dans le sélecteur de période aussi — : une date à un an d'ici
  en trois clics, pas douze. Un filtre de colonne sur une date-heure avec fuseau compare le jour local, celui
  qu'affiche la grille. Les champs date-heure du Data Explorer filtrent aussi avec lui (au jour).
- Data Explorer : un jour dans un filtre sur un champ date ou date-heure est ce jour entier — `≤` / la fin d'un
  « entre » gardent les lignes de ce jour après 00:00, `=` / `≠` veulent dire dans / hors de ce jour (ils comparaient à
  minuit).
- Dates : une date-heure portant son fuseau (un `timestamptz` PostgreSQL, `…+00:00`) s'affiche à l'heure locale du
  navigateur (`2026-10-29 00:54:08`), comme les jours du filtre de période — elle s'affichait en UTC brut, un contrôle
  fait à 00:54 à Paris se lisant la veille. Les valeurs sans fuseau (`DATE` Oracle, dates JD Edwards) restent telles
  quelles.
- La période des filtres avancés remplit son champ comme les autres saisies, à leur hauteur (elle laissait un vide et
  se plaçait plus haut que ses voisines).
- Écrans : une colonne date des filtres avancés est une période — le sélecteur de dates des tableaux de bord
  (préréglages et calendrier sur deux mois), envoyée en `<COL>_from` / `<COL>_to` — au lieu d'une date exacte. Le
  nouveau `filter_period` d'une colonne (groupe Filtre du Visual Designer) fixe le préréglage à l'ouverture de l'écran
  (`last7`… ; vide : toutes les lignes) ; le premier chargement automatique l'applique déjà. Un lien ou un rebond
  passant une date sur une colonne date ouvre ce jour-là.
- Périodes sur une colonne date : une période finissant un jour couvre tout ce jour (`< le lendemain`) — colonne
  date-heure, ou colonne date portant une heure (`DATE` Oracle, horodatage déclaré en date). Elle s'arrêtait à 00:00 — une période finissant aujourd'hui, ou un seul jour, perdait les lignes de ce jour — sur les
  écrans comme sur les tableaux de bord.
- Développement : des tests de non-régression de bout en bout (`frontend/e2e/`, Playwright, `npm run e2e`) pilotent les
  écrans de données sur un serveur réel et vérifient ce qu'ils enregistrent en base — le formulaire (ajout sur
  plusieurs onglets, duplication, suppression), l'édition en masse (ajout, modification, clonage, suppression), les
  actions du cycle de vie et le menu de ligne ; uniquement des enregistrements de test, jamais lancés en CI.
- Verrou d'enregistrement : un enregistrement ouvert dans un autre onglet ou par un autre utilisateur s'ouvre désormais
  en lecture seule avec le bandeau « verrouillé par », même quand cet onglet s'est connecté après la prise du verrou.
  Le serveur refusait bien le second verrou, mais le navigateur ne connaissait que les verrous pris pendant sa
  connexion : le formulaire restait modifiable.
- Visual Designer : un enregistrement ne réécrit que les écrans modifiés. Les autres gardent leur table telle qu'elle
  est dans `screens.toml` (les clés que le modèle ignore — un `l` au niveau de l'écran —, un `key_columns` explicite),
  et l'écran modifié est écrit sans ses valeurs par défaut, dans l'ordre des clés du fichier — modifier un libellé
  change une ligne. L'enregistrement écrivait toutes les valeurs par défaut de tous les écrans (le fichier grossissait
  de 15 % : 30 724 → 35 300 lignes sur celui des applications) et perdait les clés que le modèle ignore.
- Interne (aucun changement de comportement) : `liberty/web/admin.py` (3 800 lignes) devient le paquet
  `liberty/web/admin/`, un module par section (versions, éditeurs, assistant, catalogues de modules, thème, jobs,
  paramètres de l'application, usages / paquets, propositions, clonage, suppression, renommage…) — les routes
  s'enregistrent dans le même ordre, chaque nom reste importable depuis `liberty.web.admin` ; les fonctions de texte
  SQL du connecteur (découpage de l'ORDER BY, enveloppes d'agrégat / de filtre, marqueurs de schéma / dblink) sont dans
  `liberty/connectors/sqltext.py`. Interface : l'analyseur et le générateur de l'assistant SQL sont dans
  `services/sqlParse.ts`, les fonctions de colonne de la grille dans `resultColumns.ts` et sa cellule d'édition dans
  `EditCell.tsx` (tous testés), son menu de ligne dans le hook `useRowMenu.tsx`, son édition en masse (modifications en attente, lignes
  nouvelles / supprimées / cochées, presse-papiers, l'enregistrement et ses actions de ligne) dans le hook
  `useBatchEdit.ts`, son import (fichier, boîte d'import, collage : correspondance des en-têtes, contrôle des
  règles, report des lookups) dans le hook `useImport.ts` ; le verrou d'enregistrement du formulaire est le hook
  `useRecordLock.ts` ; la saisie avant action (`prompt_fields`), copiée dans les deux, est un seul hook
  `useActionPrompt.tsx`, et les éditeurs partagent leur cadre, leur barre d'outils et leur ligne vide
  (`common/builderShell.ts`).

## 7.0.66 — 2026-10-29

- Référence développeur → Impact : chaque entité de la configuration (pools, connecteurs, requêtes, endpoints API,
  éléments du dictionnaire, écrans, éléments de menu, graphiques, tableaux de bord, actions partagées, modèles
  Explorer, jobs, règles de notification, blocs IA) avec ce qu'elle utilise et ce qui l'utilise, de proche en
  proche — ce qu'atteint une modification, compté par type (une requête → ses écrans → les éléments de menu qui les
  ouvrent, les graphiques → leurs tableaux de bord, les jobs et actions qui l'exécutent) ; l'arbre « utilisée par »,
  les utilisations directes et tout ce dont l'entité a besoin ; un nœud recentre la vue (avec Retour), « Ouvrir dans
  l'éditeur » mène à son éditeur ; les entités que rien ne référence (points d'entrée exceptés) et les références
  vers des entités non définies sont listées (`/admin/dev/impact`). Les liens sont le parcours des dépendances de
  Package & Deploy.
- Package & Deploy / promotion : un élément de menu emporte désormais sa cible — le parcours ne suivait que les
  types d'élément de la v1 (table / form / chart), un élément `screen`, `query`, `endpoint` ou `job` n'atteignait
  que son connecteur ; un tableau de bord nommé `<app>.<id>` (comme les menus le nomment) se résout dans son
  application et non dans la première qui porte cet id (`overview` existe dans plusieurs) ; une règle de
  notification emporte l'écran ou les jobs que nomme son déclencheur.
- Référence développeur → Ligne de commande : chaque commande installée avec le paquet, lue depuis ses points
  d'entrée et son analyseur d'arguments — son rôle, son usage (à copier), les options de toutes les commandes, chaque
  sous-commande avec ses arguments (valeur, défaut, choix, obligatoire, répétable) ; à gauche, les commandes avec une
  recherche (`/admin/dev/cli`). La section ligne de commande du rapport technique en est tirée et liste désormais les
  arguments de chaque commande. `liberty-crypto` et `liberty-license` construisent leur analyseur dans
  `build_parser()` comme les autres commandes.
- Référence développeur → Impact : les groupes de gauche démarrent repliés, avec Tout déplier / Tout replier ; une
  recherche ou le filtre des inutilisées ouvre les groupes qui ont des résultats.
- Documentation de l'API : les réponses des principales routes de lecture — connecteurs (liste, description,
  exécution d'une requête, appel d'un endpoint, schémas, import), menus, écrans, adresses externes, Data Explorer
  (modèles, champs d'un modèle, requêtes agrégées et détail, analyses / tableaux de bord enregistrés) et nomaflow
  (jobs, exécution immédiate, exécutions, détail d'une exécution) — sont
  documentées (`/docs`, Référence développeur → API : la section Réponse). De la documentation seulement : déclarées à
  côté des réponses d'erreur, les routes renvoient exactement la même chose ; un test valide le JSON de chaque route
  contre son modèle pour que la documentation ne puisse pas diverger.
- Référence développeur → Ligne de commande : les commandes à gauche démarrent repliées, avec Tout déplier / Tout
  replier.
- `liberty-crypto` n'a plus de commande `decrypt` : un secret n'est jamais réaffiché en clair depuis la ligne de
  commande (un terminal, un journal, l'historique du shell gardant `--master-key`). Rien ne l'utilisait — le serveur,
  les pools et les jobs (les étapes LDAP et piste d'audit de nomasx1 qui lisent `settings_applications`) déchiffrent
  dans leur propre processus ; `encrypt` et `is-encrypted` restent. Pour changer un secret, chiffrer la nouvelle valeur.
- Rapports de documentation : chaque rapport généré est conservé sur le serveur (`<dossier de config>/.reports/`, à
  côté de `.versions/`, donc dans une sauvegarde) avec qui l'a produit, la langue et les sections ; la page du rapport
  liste ses copies pour les télécharger à nouveau ou les supprimer (`/admin/dev/report-copies`), la page Rétention
  montre leur nombre et leur taille. Une copie qui ne peut pas être écrite ne bloque jamais le téléchargement.
- Référence développeur → API : l'exemple de requête / de réponse d'une route n'est plus écrasé sur une ligne dans son
  détail (le bloc rétrécissait quand le détail défilait) ; de même pour la liste d'erreurs de l'import Excel de la
  grille.

## 7.0.65 — 2026-10-29

- Référence développeur (une nouvelle page dans la barre latérale, superutilisateur ; de la
  documentation, rien ne s'y modifie) : chaque paramètre de app.toml, lu depuis le code en cours
  d'exécution — les sections à gauche avec ce qui n'est pas encore dans l'interface, la grille à
  droite (type, l'écran qui le modifie ou pourquoi il reste dans le fichier / l'environnement, les
  modules qui le lisent, défaut ; filtres, export), et une ligne ouvre le détail avec chaque ligne de
  code qui le lit (`/admin/dev/config`).
- Paramètres → App : chaque paramètre de app.toml se règle maintenant depuis l'écran — nouvelles
  sections Journal du serveur (format console, le fichier sur disque ou désactivé, sa taille et sa
  rotation), Connexion (stockage des comptes et son pool, émetteur et durées des jetons, limitation
  des connexions), nomaflow (actif ou non, pool, lignes de journal par exécution, espace de travail,
  téléversement maximal) et Paquets de changements (actif ou non, pool, rétention) ; le sous-chemin
  (`base_path`), les attributs OIDC lus depuis les claims et le secret de session (stocké chiffré),
  les outils Explorateur de l'assistant, le pool de la boîte de réception. Ce qui reste dans le
  fichier — les chemins des fichiers de configuration, l'algorithme de signature, la clé maîtresse
  et le secret JWT — est listé en lecture seule avec la raison. Un test échoue désormais quand un
  nouveau paramètre n'est ni modifiable ni expliqué.
- Paramètres → App : modifier seulement les paramètres de notification active maintenant
  Enregistrer (ils n'entraient pas dans la détection des modifications).
- Référence développeur → API : chaque route du serveur en cours d'exécution — méthode, chemin,
  résumé, le module auquel elle appartient (par catégorie : Accès, Espace de travail,
  Fonctionnalités, nomaflow et déploiement, Paramètres, Exploitation, Assistant) et l'accès qu'elle
  exige, lu depuis le code (public, tout utilisateur connecté, superutilisateur, ou la permission
  vérifiée comme `sql:{connector}:{query}`) ; une route s'ouvre sur son gestionnaire (fichier:ligne),
  sa description, ses paramètres, sa requête (ses champs et un exemple JSON) et sa réponse (depuis
  les modèles déclarés) ; liens vers /docs et /redoc (`/admin/dev/api`). La
  Configuration affiche maintenant le libellé de chaque paramètre tel que dans son écran.
- Référence développeur → Modèle de données : les tables du framework (comptes, nomaflow, paquets de
  changements, vues enregistrées, notifications — depuis leurs modèles : colonnes, types, clés,
  références) et les tables déclarées par chaque connecteur — la table physique de sa requête de
  lecture, sa clé, ses requêtes, les écrans / tableaux de bord / graphiques / tâches / modèles de l'Explorateur qui l'utilisent, les
  tables liées (les tables imbriquées des écrans, cliquables) et ses colonnes avec les libellés du
  dictionnaire et les écrans qui les montrent (une table qu'aucun écran ne montre — la requête d'un
  tableau de bord — lit ses colonnes dans la liste du SELECT de son SQL) (`/admin/dev/data`). Configuration : l'en-tête compte ce
  qui se modifie dans un écran, ce qui reste dans le fichier / l'environnement et ce qui n'est pas
  dans l'interface, et un filtre d'état remplace la case à cocher.
- L'espace Liberty Next (superutilisateur) : les pages du framework ne sont plus listées sous le menu de chaque
  application — elles forment un espace à part, choisi dans le sélecteur d'applications comme une application ou par le
  nouveau bouton de la barre du haut (qui ramène à la dernière application). La page Paramètres est éclatée : chaque
  éditeur est sa propre page (`/liberty/<page>`, chacune avec le bouton « recharger la configuration »), regroupées par
  usage — Construire, dans l'ordre où se monte une application (assistant d'écrans, connecteurs, dictionnaire, écrans,
  menus, actions, graphiques, tableaux de bord, modèles de l'Explorateur, modèles de rapport, règles de notification,
  IA, intégrité), Administrer (app, pools, accès,
  thème), Exploiter (supervision, tâches), Déployer (paquet, modifications, historique), Documenter (référence
  développeur, rapports de documentation, notes de version). Paquet, Modifications et Intégrité quittent l'application
  nomaflow (liberty-apps 0.1.3). Les anciens liens `/settings?tab=…` et `/nomaflow/package | changes | integrity`
  redirigent vers les nouvelles pages ; les éditeurs s'ouvrent sur la dernière application utilisée ; les messages qui
  renvoyaient à « Paramètres → … » nomment le nouvel emplacement.
- Liberty Next → Documenter → Rapports de documentation (superutilisateur) : le rapport d'implémentation et le rapport
  technique, choisis par deux cartes ; les sections à inclure, la langue, le libellé de date de la page de garde, PDF ou Markdown
  (`/admin/dev/reports`). Le rapport technique documente la version à partir du code en cours d'exécution
  (architecture, modèle de données, référence de configuration et ses écarts, API avec l'accès de chaque route, lignes
  de commande, paquetage, références dans le code) ; le rapport d'implémentation documente l'installation (composants
  actifs, applications et menus, écrans, connecteurs et pools avec leur état de chargement, tableaux de bord /
  graphiques / modèles de l'Explorateur, tâches, règles de notification, rôles et utilisateurs, dictionnaire, réglages
  et API en annexe), secrets masqués.
- Rapports PDF : un tableau aux identifiants longs (noms de requêtes, chemins) ne dépasse plus la marge
  droite — les cellules les coupent après `_`, `.` ou `/`. Référence développeur → Configuration et le rapport
  technique affichent un chemin par défaut sous la forme `${LIBERTY_APPS_DIR}/…` au lieu du chemin absolu du serveur.
- Installable comme une application (PWA) : Chrome / Edge proposent « Installer » et Safari « Ajouter au Dock » —
  Liberty Next s'ouvre dans sa propre fenêtre, au nom de l'instance (le nom d'application du thème). Le service
  worker ne met rien en cache : chaque requête part au serveur comme dans le navigateur, une nouvelle version est donc
  prise au chargement suivant et aucune donnée ne reste sur le poste. Nécessite HTTPS (ou localhost) ; fonctionne
  sous `[app] base_path`. Icônes 192 / 512 px, maskable et Apple.
- nomaflow sans superutilisateur : `nomaflow:view` suit les tâches (liste, planning, exécutions avec leur journal et
  leurs fichiers), `job:<id>` (`job:*` pour toutes) lance une tâche, dépose son fichier et annule ses exécutions — la
  permission qu'une entrée de menu de tâche demande déjà — et `nomaflow:edit` crée, modifie, supprime, suspend et
  reprend les tâches et applique le changement (`POST /admin/jobs/reload`, la partie tâches du rechargement) ; edit
  inclut view. Les politiques de rétention / d'exécutions manquées, leurs lancements manuels et l'export d'une tâche
  restent au superutilisateur. Accordé dans Accès → Autres accès → nomaflow ; les pages nomaflow du menu n'apparaissent
  qu'à qui détient view (ou une autorisation de menu), et l'application nomaflow apparaît dans le sélecteur avec son
  connecteur (`sql:nomaflow:*`). Modifier les tâches est un droit fort : leurs étapes exécutent du SQL et du Python
  autorisé sans restriction de lignes. La fenêtre « Lancer avec paramètres » ne lit que la tâche lancée
  (`GET /admin/jobs/{id}/definition`).
- Catalogues de modules (catalogue v2, étape 1) : un bloc `[[modules]]` à côté des presets décrit un module
  d'application entier — un écran par table avec sa clé, ses colonnes de grille, ses onglets de dialogue et ses onglets
  de détail (l'écran enfant et la clé qu'il hérite), et le dossier de menu — colonnes nommées par élément de données, le
  préfixe JDE résolu plus tard depuis le schéma réel (`liberty/modules.py` : modèle, chargement, validation du graphe
  des détails). Appliquer un module (`/admin/assistant/modules`, `…/{id}/preview`, `…/{id}/apply`,
  superutilisateur) : l'aperçu lit les tables réelles et donne, par écran, le préfixe trouvé, les colonnes, les éléments
  de données absents de la table, ce qui est nouveau et ce qui existe ; l'application écrit les nouvelles requêtes de
  lecture (`<écran>_get`, triées par la clé), les écrans en lecture seule avec leurs onglets de détail (filtrés sur la
  clé héritée, masquée), le dossier et les entrées de menu — en ajout, l'existant n'est jamais écrasé, les trois
  fichiers validés avant toute écriture — puis les entrées de dictionnaire de ses tables sous le périmètre de
  l'application (libellés JDE, formats, listes UDC) et un rechargement. Une table ou une colonne clé introuvable le
  bloque. Un détail peut être un-à-un (`cardinality = "one"` : le formulaire de l'enfant en onglet, pas une grille) ;
  un écran peut joindre une table de référence pour quelques colonnes (`[[modules.screens.joins]]` — le nom de
  l'adresse sur la liste des clients), placées dans l'ordre de colonnes du module ; `filters` nomme les colonnes du
  panneau de filtres avancés (filtrées en base avant le chargement des lignes, colonnes jointes comprises) ;
  `?replace=1` réécrit sur place les
  requêtes, écrans et entrées de menu du module.
- Un écran `read_only` avec un dialogue l'ouvre de nouveau sur une ligne (en consultation : champs désactivés, ni
  Enregistrer ni Supprimer, ses onglets de détail en lecture seule) — la grille n'ouvrait le dialogue que si l'écran
  avait une requête de modification ou de création.
- Assistant d'écrans → Un module : le catalogue des modules (recherche, groupes), l'aperçu lu sur les tables réelles
  (par écran, en arbre de ses détails : table, préfixe, colonnes, éléments de données absents de la table, nouveau /
  existe / remplacé ; le menu ; ce qui bloque), Remplacer avec son avertissement, Appliquer — la barre latérale
  rafraîchie, puis Ouvrir le premier écran du module.
- Les onglets de détail (table / formulaire imbriqués) prennent leurs valeurs de lien dans toute la ligne chargée, pas
  seulement dans les champs du dialogue — une colonne de lien tenue hors du formulaire (la clé d'un détail) était
  perdue, et l'onglet filtrait alors sur une partie de sa clé et montrait les lignes d'autres enregistrements (Contacts →
  E-mail listait l'e-mail de ligne 1 de toutes les adresses) ; un onglet au lien incomplet ne charge plus rien.
- Un écran `read_only` ne pose plus de verrou d'enregistrement (il n'écrit rien ; son dialogue affichait « Verrouillé
  par … » au lecteur suivant).
- Un champ de liste en lecture seule affiche sa description à côté du code, et une cellule de liste dans la grille
  trouve sa description quand le code stocké est plus court que la clé UDC JDE (`"C"` contre `"       C"`) — la
  comparaison tolérante aux espaces que la liste déroulante appliquait déjà (`matchOption`).
- Une vue de grille partagée qui liste une colonne LOOKUP / ENUM garde sa colonne de description juste après
  (`<col>__lookup`), sauf si la vue la place elle-même — une vue nommant le code masquait le libellé que la grille
  affiche à côté par défaut.
- Appliquer un module : les colonnes de la grille vont dans une vue par défaut ; les colonnes du seul dialogue ne sont
  plus `hidden` (le dialogue retirait le champ d'une colonne masquée — les onglets Indicateurs / Codes catégorie /
  Adresses liées étaient vides).
- Action `open_url` : ouvrir une adresse externe depuis un menu de ligne, un bouton de barre d'outils, un onglet de
  dialogue, une colonne d'exploration ou une chaîne — `url` est un modèle (`{COLONNE}` depuis la ligne / le formulaire,
  les champs de saisie, les chemins de chaîne, encodés ; `#LOGIN_USER#` et les autres variables ; un premier
  paramètre est l'adresse elle-même), `new_tab` (activé par défaut), la saisie avant
  exécution de `navigate`. Seules les adresses http(s) et les chemins de ce serveur s'ouvrent — refusé au chargement et
  vérifié de nouveau sur l'URL remplie. Un `open_url` seul dans un menu de ligne part de la poignée de la ligne. Une
  entrée de menu `url` ouvre une adresse dans un nouvel onglet (affichée sauf refus du menu). Les éditeurs d'actions la
  proposent.
- Liberty Next → Administrer → App : les réglages `[app]` en trois groupes — Serveur (nom, hôte, port, niveau de
  journal, rechargement à chaud, mode Oracle thick, sous-chemin), Affichage (langue et période par défaut, taille de
  page, cache des tableaux de bord, onglets, explorations) et Adresses externes ; app.toml inchangé. La page range
  ses sections par ordre alphabétique (de la langue courante), la liste en lecture seule du déploiement en dernier.
- Adresses externes (`[app] external_urls`, Liberty Next → Administrer → App) : les adresses nommées des systèmes vers
  lesquels l'installation renvoie — `JDE_WEB = "https://jde:8080/jde"` — employées en `#URL.JDE_WEB#` au début d'un
  `open_url` / d'une entrée de menu `url`, pour que les écrans livrés ne portent aucune adresse client ; servies aux
  utilisateurs connectés (`/api/external-urls`), actives dès l'enregistrement. Une adresse non renseignée ne donne
  aucune URL (refusée, jamais ouverte comme chemin local).
- Catalogues de modules : le `row_menu` d'un écran (comme dans screens.toml) ; un paramètre `{ÉLÉMENT}` d'un `open_url`
  nomme un élément de données et devient la colonne physique (`{AN8}` → `{ABAN8}`).
- Éditeur de jobs nomaflow : enregistrer un job ne réécrit que les jobs modifiés. L'éditeur reçoit chaque job avec
  ses références `${…}` résolues, et un enregistrement les réécrivait toutes — les chemins d'un serveur
  (`${LIBERTY_APPS_DIR}/…` devenu `/srv/…`) se retrouvaient dans tous les autres jobs du fichier ; les jobs inchangés
  gardent maintenant leur texte tel qu'il est sur disque (commentaires, `${…}`, `step_defaults`), et le job modifié
  retrouve ses références `${…}` là où la valeur correspond encore.

## 7.0.64 — 2026-10-28

- Paquets et déploiement : un import peut être prévisualisé (`/admin/import-package?dry_run=true`) :
  le plan liste chaque élément — nouveau, modifié (avec ses champs avant / après), identique,
  conservé (`override = true` ici), ignoré, supprimé — avec des avertissements quand le paquet
  vient d'un framework plus récent ou porte des valeurs `ENC:` que la clé maîtresse de cette
  instance ne sait pas déchiffrer. Une application renvoie le même plan.
- Paquets et déploiement : un paquet porte aussi les actions partagées, les modèles de
  l'Explorateur de données (avec leurs analyses), les règles de notification, les tâches nomaflow
  et les questions de départ et procédures de l'assistant d'une application. Une tâche qui existe
  déjà sur la cible garde sa planification, son activation, son fuseau, ses préréglages, ses
  alertes, ses reprises, son niveau de journal et son délai ; une nouvelle tâche arrive désactivée.
- Paquets et déploiement : un paquet est construit à partir des fichiers tels qu'écrits (valeurs
  non renseignées et références `${VAR}` conservées), et les requêtes, tables, séquences, listes de
  valeurs et points d'accès d'un connecteur sont importés un par un — importer un paquet ne
  remplace plus un connecteur entier (ses autres requêtes étaient perdues), et les requêtes d'un
  écran partent même quand son connecteur n'est pas sélectionné. `connectors.toml` et `jobs.toml`
  gardent leurs commentaires à l'import.
- Paramètres → Paquet : Importer prévisualise le paquet dès qu'il est choisi (et à nouveau quand la
  stratégie change) : le nombre par état, les avertissements, puis les éléments de chaque fichier —
  ce qui change d'abord, les identiques sur demande — et un élément modifié s'ouvre sur ses champs,
  ici maintenant / après l'import ; « Appliquer N changement(s) » l'écrit ensuite et affiche le
  résultat. Construire propose les nouveaux types : modèles de l'Explorateur, tâches nomaflow,
  règles de notification, actions partagées, l'assistant par application.
- Promotion vers une autre instance (API ; les écrans suivent) : `[promotion.targets.<nom>]`
  dans app.toml — l'URL de la cible (avec son sous-chemin) et un compte local superutilisateur
  là-bas, le mot de passe stocké chiffré (`/admin/promotion/targets`, un test de connexion par
  cible). `POST /admin/promotion/push` construit un paquet de configuration (la sélection de
  Paquet ; connecteurs, pools et points d'accès d'API restent ceux de la cible sauf s'ils sont
  choisis) ou prend un paquet de changements approuvé, se connecte à la cible et renvoie son aperçu
  — ou l'y applique. Une promotion appliquée est gardée dans un historique (`<config>/.promotion/`,
  `/admin/promotion/history`) et marque le paquet de changements comme exporté.
- « Promouvoir vers… » dans Paramètres → Paquet (Construire) et sur un paquet de changements
  approuvé (nomaflow → Changements) : choisir une instance cible — elle prévisualise aussitôt (le plan
  du paquet avec ses différences, ou le rapport du paquet de changements où un conflit ou un
  changement invérifiable peut être forcé) — puis « Appliquer sur <cible> ». Paquet → Promotion
  édite les instances cibles (Tester s'y connecte) et liste les promotions appliquées depuis ici ;
  une ligne s'ouvre sur le rapport de la cible.
- Changements → Appliquer : un changement dont la dérive ne peut pas être vérifiée (pas de requête
  de lecture) peut être forcé — il ne s'appliquait jamais, la case Forcer n'apparaissait que sur
  les conflits.
- Déploiement : la base de données peut vivre sur un serveur PostgreSQL hors de la pile. À
  l'installation, `./liberty install full --db external --db-host H [--db-port P] [--db-user U]
  [--db-name D] [--db-password-env VAR]` ne démarre aucun Postgres embarqué ; plus tard,
  `./liberty db external --host H …` déplace une installation (le pool par défaut réécrit, les
  pools qui nommaient le serveur embarqué — ceux des applications — déplacés, le Postgres embarqué
  arrêté avec son volume conservé) et `./liberty db embedded` revient en arrière. Enregistré dans la
  chaîne compose (`docker-compose.external-db.yml`, `docker-compose.no-embedded-db.yml`) et `.env`,
  donc démarrage / mise à niveau / sauvegarde le suivent ; `./liberty db dump` fait un
  `pg_dumpall` du Postgres embarqué pour transporter les données ; `status`, `doctor` et `backup`
  indiquent où est la base. Nouvelles commandes `liberty-admin set-default-pool` et
  `retarget-pools`.

## 7.0.63 — 2026-10-28

- Supervision → Journal du serveur : le journal du serveur est désormais conservé sur disque — un
  fichier JSON-lines à rotation par taille (`[app] log_file`, par défaut
  `<dossier de config>/.logs/liberty.log` ; `log_file_max_mb` 10, `log_file_backups` 5) alimenté
  par le framework, les applications et les avertissements des bibliothèques, quel que soit le
  lanceur du serveur ; une rafale du même message est écrite une fois avec « repeated N times ».
  La page le recherche (niveau, module, texte du message / module / exception / identifiant de
  requête, période), ouvre une exception, filtre sur une requête, exporte les résultats en texte
  et télécharge les fichiers ; les niveaux par module changent aussitôt et sont enregistrés dans
  `[app] log_levels` (`/admin/logs`, super-utilisateur). La vue en direct reste le premier onglet.
- Supervision → Rétention : tous les stockages que le serveur fait grossir au même endroit —
  exécutions et fichiers des tâches, notifications, versions de configuration, paquets de
  modifications, vues enregistrées, journal du serveur — avec ce qu'ils gardent, où c'est réglé,
  leur taille, un Aperçu (simulation : ce qui partirait, rien n'est supprimé) et Purger
  (`/admin/retention`, super-utilisateur). Les paquets de modifications ont une politique :
  `[changesets] retention_days` (0 = garder, par défaut) supprime les paquets exportés / promus et
  les journaux d'import plus anciens, contrôlé avec la rétention des notifications toutes les six
  heures ; les brouillons ne sont jamais touchés. Les vues enregistrées sont le travail des
  utilisateurs : affichées, jamais purgées.
- Barre latérale : « Ouvrir au survol » — le menu se replie sur ses icônes et s'ouvre par-dessus
  la page quand la souris passe dessus (choisir une page le replie) ; « Garder ouvert » revient au
  menu fixe ; mémorisé par navigateur.
- Menus : une page d'accueil par rôle — `home_by_role = { auditor = "<élément de menu>" }` sur le
  connecteur d'une application : l'utilisateur arrive sur l'élément de son premier rôle listé,
  sinon sur `home`.
- Un manifeste d'application web installable (`/manifest.webmanifest`, public) au nom de
  l'instance (le nom d'application du thème, sinon `[app] name`).
- Profil : plus d'onglet Sécurité pour une session en authentification unique (pas de mot de
  passe local).
- Listes déroulantes : les options d'un groupe se rangent sous un en-tête par groupe — les modèles
  de l'Explorateur par module de préréglage (« JDE · Accounts Payable » : le `group` du préréglage,
  ou celui du modèle), sinon par application (la liste fermée montre aussi le groupe), les colonnes
  d'une règle de périmètre par connecteur ; un × facultatif efface ; une taille compacte. Une liste à plusieurs
  valeurs (puces sur le déclencheur, « Tout ce qui est affiché », Effacer), utilisée d'abord pour
  les tâches d'une règle de notification.
- Un sélecteur de variables à côté des champs à modèle : le bouton { } liste les variables et
  insère celle choisie au curseur — sujet, corps et lien d'une règle de notification (les champs
  de l'événement de tâche ou d'écran), sujet et corps d'une étape de notification.
- Filtres de colonne des grilles : une colonne codée (une énumération) choisit plusieurs valeurs à
  la fois et accepte un code saisi hors de sa liste ; une colonne à liste de valeurs garde son
  sélecteur simple. Une vue enregistrée avec une seule valeur s'ouvre toujours.
- Grilles : `wrap = true` sur une colonne d'écran renvoie le texte long à la ligne au lieu de le
  couper ; le `page_size` d'un écran fixe les lignes par page de sa grille (sinon
  `[app] default_page_size`) ; le choix des lignes par page est la liste déroulante du thème.
- Menu Vues : « Rétablir la version livrée » ramène la grille à la vue partagée par défaut de l'écran,
  sinon à sa mise en page de base.
- Service sous un sous-chemin derrière un proxy inverse : `[app] base_path = "/liberty"`
  (redémarrage) — la page, ses ressources, l'API, les téléchargements, la connexion temps réel et
  les liens se résolvent sous ce préfixe, que le proxy le retire ou non ; les adresses construites
  par le serveur (le retour d'authentification unique) le portent. Tous les téléchargements et
  envois passent désormais par le client d'API (le rafraîchissement de session s'applique).
- Correction : nomaflow → Tâches → Exporter télécharge de nouveau le paquet de la tâche (l'appel
  visait une mauvaise adresse, sans la session, et ne faisait rien).
- Supervision, Paramètres → Paquet et Paramètres → Accès choisissent leurs vues dans une rangée de
  cartes — une icône, le nom et une ligne sur l'usage de la vue — au lieu de simples onglets.

## 7.0.62 — 2026-10-28

- nomaflow : le classeur / CSV téléversé pour une exécution peut être retéléchargé — sous l'étape
  `file_load` du détail d'exécution, et sur la page de tâche de l'utilisateur (« Fichier
  chargé »). Les fichiers téléversés restent dans l'espace de travail de la tâche ;
  `GET /admin/jobs/runs/{run}/inputs/{step}` et `GET /api/jobs/{job}/runs/{run}/inputs/{step}`
  les servent.
- nomaflow : le balayage de rétention supprime aussi les anciens fichiers de l'espace de travail
  des tâches — fichiers téléversés, rapports de rejets, rapports, exports et archives `done/` /
  `failed/` des dossiers surveillés par file_load — plus anciens que `[meta.retention] files_days`
  (vide = les `days` des exécutions, 30 par défaut ; 0 = garder les fichiers). **À la mise à jour,
  les fichiers de plus de 30 jours partent au premier balayage** si `files_days` n'est pas réglé ;
  la copie d'un export dans son `export_dir` n'est jamais touchée. Réglage dans le panneau
  Rétention ; le balayage indique les fichiers et octets supprimés. L'enregistrement de la
  politique de rétention n'échoue plus sur un champ vide.

## 7.0.61 — 2026-10-28

- Tâches : une étape de rapport de plugin (`report:<scope>:<id>`) reçoit les paramètres de la
  tâche et les paramètres utilisateur de l'exécution par-dessus ses propres `params`, typés selon
  les paramètres déclarés du rapport.
- Tableaux de bord : la page grandit et défile jusqu'en bas ; une carte de liens pleine largeur
  montre tous ses liens ; plus d'icônes de barre latérale pour les menus des applications.
- Tâches : un paramètre utilisateur sans `step` est un paramètre de la tâche, passé à toutes les
  étapes ; ses choix peuvent venir d'une requête de lecture
  (`choices_from = "query:<connecteur>/<requête>:<valeur>:<libellé>"`). La boîte « Exécuter avec
  paramètres » de l'administration propose les mêmes listes.
- nomaflow : la liste des tâches se cherche (id, description, étiquette), se filtre par
  application, statut et étiquette (mémorisés par navigateur), se groupe par application en
  groupes repliables, une ligne par tâche avec des actions en icônes.
- Tâches : un élément de menu `job` peut fixer un des préréglages de la tâche
  (`params = { preset = "…" }`) ; la page de l'utilisateur la lance avec les paramètres et les
  étapes de ce préréglage.
- nomaflow : la page Planning montre les prochaines exécutions jour par jour, la planification en
  clair (« chaque jour à 03:00 »), puis les planifications désactivées ; les tâches lancées à la
  main uniquement sont repliées. Plus de bouton « Retour aux tâches ». La barre latérale ne
  surligne plus Tâches sur la page Planning.
- nomaflow : choisir un préréglage dans « Exécuter avec paramètres » part des valeurs enregistrées
  de la tâche, comme le planificateur ; il ne garde plus les étapes désactivées par le préréglage
  précédent. Les tâches étiquetées `main` sont listées en premier, dans un groupe « Tâches
  principales ».
- Fusion de configuration : `merge_jobs` / `merge_jobs_file` fusionnent un `jobs.toml` livré
  tâche par tâche — étapes du produit mises à jour, champs de l'opérateur (activation,
  planification, fuseau, préréglages, alertes, reprises, niveau de log, délai, valeurs de
  paramètres) conservés ; une tâche reçoit un indicateur `override`. Utilisé par l'installateur
  des applications.
- Tâches : `for_each = "<param>"` lance une tâche une fois par valeur quand ce paramètre en
  porte plusieurs (une liste, `"1,3"`, ou `"all"` = tous les choix du paramètre utilisateur de ce
  nom), une exécution après l'autre, chacune avec une seule valeur ; une planification ou un
  préréglage avec `"all"` couvre toutes les valeurs. Un paramètre utilisateur `multiple = true`
  s'affiche en cases à cocher avec « Tout », sur la page de l'utilisateur et dans « Exécuter avec
  paramètres ».
- nomaflow : une page Exécutions (`/nomaflow/runs`) remplace la grille générique : recherche,
  filtres tâche, période et état (les pastilles d'état comptent les exécutions), exécutions
  groupées par jour avec leurs paramètres (l'application d'une exécution par application),
  déclencheur, durée, lignes, l'erreur sous une exécution en échec et une barre de progression
  pendant l'exécution ; elle se rafraîchit seule tant qu'une exécution tourne. S'appuie sur
  `GET /admin/jobs/runs`. La page Détail d'exécution perd ses boutons retour.
- Barre latérale : une icône de menu `shieldalert`.
- Connecteurs : un champ peut porter une référence `${ENV}` même booléen ou numérique
  (`verify_ssl = "${JDE_AIS_VERIFY_SSL:-true}"`) : l'enregistrement des connecteurs dans les
  Paramètres, la suppression avec dépendances, la génération et le test d'API valident désormais
  les valeurs telles que l'exécution les résout (le fichier était refusé avant). L'éditeur de
  connecteur API affiche et conserve une telle référence pour la vérification TLS.
- Fusion de configuration (mise à jour des applications, Package & Deploy) : une valeur livrée
  qui est entièrement une référence `${VAR}` ou `${VAR:-défaut}` appartient à l'installation —
  la valeur en place l'emporte, booléens, nombres et référence propre à l'installation compris
  (plusieurs connexions AIS, chacune sur sa variable). Avant, une vérification TLS
  désactivée dans les Paramètres était réactivée.
- Tâches : une échéance — `alerts.finish_by = "07:00"` (fuseau de la tâche). Un jour où la tâche
  est planifiée, une exécution réussie doit être terminée à cette heure ; sinon le nouvel
  événement de tâche `late` part vers les règles de notification et les destinataires d'alerte de
  la tâche, en disant si l'exécution tourne encore, a échoué ou n'a pas démarré. Contrôlé avec les
  planifications manquées ; réglé dans l'éditeur de tâche (Alertes).
- Tableaux de bord : les graphiques et tableaux portent leur titre et leur description dans leur
  carte, comme les autres widgets.
- nomaflow : un rechargement ne laisse plus le « Tout » d'une exécution par valeur sur les
  connecteurs précédents.
- Tableaux de bord : un widget `jobs` — les tâches nomaflow (les ids listés, ou toutes les tâches
  planifiées, par étiquette) avec la prochaine exécution ou « en pause », la planification en
  clair, le dernier état, et Exécuter / Pause / Reprendre. Réservé aux super-utilisateurs (retiré
  pour les autres) ; l'éditeur de tableau de bord le propose.
- nomaflow : mettre en pause ou reprendre une tâche (`POST /admin/jobs/{id}/enabled`) modifie
  cette tâche dans jobs.toml et réenregistre sa planification, sans rechargement complet ; la page
  Tâches l'utilise aussi. Le bloc Alertes de l'éditeur de tâche est de nouveau aligné.
- Tâches : une étape `file_export` — une requête de lecture nommée écrite dans un fichier xlsx ou
  csv de l'espace de travail de l'exécution (l'étape `notify` le joint, le détail d'exécution le
  télécharge), copié en option dans `export_dir`. Paramètres liés depuis ceux de l'exécution et
  des `params` avec modèles ; le nom du fichier et le dossier acceptent `${params.x}` /
  `${run.id}` ; les lignes arrivent en flux depuis la base, les en-têtes sont les libellés des
  colonnes. Un formulaire dans l'éditeur d'étapes.
- nomaflow : sur la page de tâche d'un utilisateur, le bouton Exécuter est aligné sur les champs.

## 7.0.60 — 2026-10-28

**Explorateur de données : modèles fournis par les préréglages, entrées de dictionnaire en un clic**
- Un préréglage de l'assistant écran portant `app` + `explorer = true` fournit un modèle
  d'explorateur par défaut (`explorer.<app>.<id du préréglage>`) : listé avec les autres,
  construit depuis le préréglage à la première utilisation, jamais écrit dans `explorer.toml`.
  Déclarez un modèle sur le même préréglage pour le personnaliser. Une table du préréglage
  accepte un `label` (le libellé de l'entité) et une `cardinality` (`one` pour une table de
  référence jointe à un pivot transactionnel : une simple jointure LEFT).
- Le catalogue JDE reçoit huit nouveaux fichiers de préréglages — Comptabilité générale,
  Fournisseurs, Clients, Stocks, Ventes, Production, Données techniques, Coûts de revient —
  issus des modèles de données Oracle E1 ; les quinze préréglages « complets » (Carnet d'adresses
  compris) fournissent leur modèle.
- Appliquer un préréglage dans Paramètres → Explorateur crée les entrées de dictionnaire qui
  manquent à ses tables (l'étape Dictionnaire de l'assistant, rangées sous l'application : une
  source JDE reçoit les descriptions de son dictionnaire de données). Sur la page Explorateur,
  un superutilisateur voit combien de colonnes affichent leur nom brut et crée les entrées en un
  clic (`POST /admin/explorer/{app}/{model}/dictionary`).

**Montants JDE : les décimales du dictionnaire de données**
- Nouveau format de dictionnaire `jdenumber` avec un champ `decimals` sur l'entrée : un montant
  JD Edwards stocké sans point décimal (F9210.FRCDEC) se lit en valeur réelle, se réécrit en
  entier stocké, et les filtres / bornes de l'explorateur sont liés en unités réelles ; les
  sommes et colonnes de détail de l'explorateur sont mises à l'échelle en SQL, les colonnes de
  résultat portent `decimals` et la table de l'explorateur affiche exactement ce nombre de
  décimales. Sur les autres formats numériques, `decimals` est le nombre de décimales affichées.
- L'analyse du dictionnaire (assistant, page dédiée, chemin des préréglages de l'explorateur) lit
  `DD_DECIMALS` dans la requête JDE par table ; les tables de revue reçoivent une colonne
  Décimales. La requête `dictionary_data_items_for_table_get` des applications émet désormais les
  types `jdenumber` / `jdedate` / `integer`, les décimales, la taille et la règle de justification
  à côté de la règle de recherche UDC.

**Des tâches pour les utilisateurs : un élément de menu qui lance une tâche et montre son journal**
- Type d'élément de menu `job` (cible = l'identifiant de la tâche nomaflow, permission
  `job:<id>`, `!job:<id>` le masque) : la feuille ouvre la page de la tâche — la description, un
  sélecteur de fichier par étape `file_load`, Lancer, le journal suivi en direct, les exécutions
  précédentes avec leur état, leur durée et leurs fichiers. Routes sous `/api/jobs/{id}` (la
  tâche, l'envoi de fichier, le lancement avec les seuls fichiers envoyés, le détail et les
  fichiers d'une exécution) ; rien d'autre du plan de contrôle nomaflow n'est exposé.
  Paramètres → Menus propose les tâches comme cibles ; Paramètres → Accès a une famille de
  règles Tâche.
- Les applications livrent les menus : nomademo reçoit un dossier Import (articles, sociétés,
  un mois de ventes, recalcul des périodes), nomasx1 un dossier Tâches (collecte sécurité,
  collecte licences), nomajde un dossier Tâches (les deux refusions de sécurité).
- Le tableau de bord Vue d'ensemble de nomaflow prend une période (7 derniers jours par
  défaut) : les chiffres, les exécutions par jour, ce qui demande attention, les exécutions et
  durées par tâche et les exécutions récentes la suivent ; « En cours » reste en direct.
- Le rafraîchissement du dictionnaire des modèles de l'Explorateur est aussi une tâche :
  `liberty.jobs.dictionary:refresh_model_dictionary` (`app`, `models` facultatif) analyse chaque
  table des modèles de l'application, écrit les entrées manquantes et reconstruit les connecteurs,
  les tables et entrées dans le journal de l'exécution. Les étapes Python reçoivent le runtime
  sous `app_state` quand elles le déclarent. nomajde la livre sous `nomajde-dictionary-refresh`
  dans son menu Tâches.
- Une tâche déclare ce qu'un utilisateur peut régler en la lançant depuis sa page :
  `[[jobs.user_params]]` (`name`, `label` + `l`, `step`, `default`, `required`, `choices` fixes
  ou `choices_from = "explorer_models:<app>"`). La page affiche une liste ou un champ texte par
  paramètre ; la valeur atteint les paramètres de l'étape, tout le reste est refusé. Le
  rafraîchissement du dictionnaire de nomajde prend le modèle Explorateur à rafraîchir (vide =
  tous les modèles).

**Tableaux de bord : widgets texte et liens — une vue d'ensemble pour une application de gestion**
- `text` : un bloc Markdown (à quoi sert l'application, comment lire la page) ; `links` : une
  carte de points d'entrée, chacun un élément de menu de l'application du tableau de bord
  (affiché seulement si l'utilisateur peut l'ouvrir, sous le libellé de l'élément par défaut)
  ou une URL. Les deux dans l'éditeur de widgets, traduisibles, exportés (le texte tel quel,
  les liens en table).
- nomajde reçoit sa Vue d'ensemble : ce que fait Nomajde, les chiffres de l'instance JDE
  (utilisateurs, rôles, enregistrements de sécurité, codes UDC, objets) et les points d'entrée de
  chaque domaine ; l'application s'ouvre dessus.

**Assistant IA : l'Explorateur de données comme outils**
- `explorer_models`, `explorer_fields` et `explorer_query` permettent à l'assistant de répondre à
  un total, un classement, une tendance ou une comparaison via les modèles de l'Explorateur
  (dimensions, mesures, filtres, période, tri, Top N) dans le périmètre de l'appelant, au lieu
  d'annoncer qu'aucune requête agrégée n'existe. Actifs par défaut (`[ai] explorer_tools =
  false` pour les retirer).
- L'assistant enregistre aussi ce que l'utilisateur demande : `explorer_saved`,
  `explorer_save_analysis` (la définition est exécutée une fois avant d'être enregistrée, privée
  sauf partage) et `explorer_save_dashboard` (des tuiles d'analyses enregistrées). La page
  Explorateur publie son contexte au chat (le modèle, l'analyse sur le canevas) et les questions de
  départ `[ai.<app>.screens.explorer]` s'y affichent.

**Onglets de l'espace de travail : une politique opérateur** (`[app]` dans app.toml, Paramètres → App)
- `tabs_multi` (désactivé : une page à la fois, en ouvrir une autre la remplace),
  `tabs_close_on_app_switch` (les onglets se ferment quand l'opérateur choisit une autre
  application, le nouvel accueil conservé) et `tabs_max` (5 par défaut ; le plus ancien onglet se
  ferme à la limite).

**Grille : une poignée visible pour le menu de ligne**
- Chaque ligne d'un écran doté d'actions `row_menu` porte une poignée discrète dans sa première
  colonne (elle reste visible sur une table large), plus nette au survol : une action `navigate`
  seule se déclenche aussitôt (la poignée porte son libellé), plusieurs actions ouvrent le même
  menu que le clic droit. Rien d'autre ne change dans la mécanique de navigation.
- Navigation sur place, en option : une action `navigate` avec `inline = true` ouvre sa cible
  (une requête ou un écran, avec les liaisons de la ligne) dans un panneau à l'intérieur de
  l'onglet courant, par-dessus la grille d'origine, avec un fil d'Ariane et un bouton Retour ; la
  grille derrière garde ses lignes et ses filtres, et une navigation depuis le panneau s'emboîte de
  la même façon. `[app] drill_inline` (Paramètres → App) fixe le comportement de toute
  navigation dont l'action ne dit rien ; l'option `inline` de l'action l'emporte dans les deux
  sens. Désactivé par défaut : rien ne change tant que l'opérateur ne l'active pas.
- Cellules-liens : l'indication de colonne `drill = "<id d'action du menu de ligne>"` affiche la
  valeur comme un lien qui déclenche cette action `navigate` pour la ligne — la colonne clé est la
  navigation, sans poignée ni menu. Automatique aussi : un écran avec une seule action `navigate`
  et une colonne clé obtient le lien sur sa première colonne clé sans aucun réglage. Paramètres →
  Écrans propose les actions du menu de ligne dans le champ ; un identifiant inconnu est refusé au
  chargement.
- Tableaux de bord : l'étiquette d'une ligne de liste se place sur la ligne du titre, la ligne
  reste sur deux hauteurs.

## 7.0.59 — 2026-10-28

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
- **Outils d'exploitation** (`[ai] ops_tools`, désactivés par défaut, une case sous App →
  Assistant IA) : `jobs_status` (les tâches nomaflow avec planification, dernier et prochain
  passage ; les derniers passages d'une tâche avec l'étape en échec), `server_log` (les lignes
  de journal récentes, par niveau et texte) et `integrity_checks` (les contrôles de cohérence de
  la configuration). L'assistant lit le serveur au lieu de deviner quand quelque chose a échoué,
  est en retard ou manque. Désactivés par défaut car le journal peut contenir ce que le serveur
  a consigné.
- **Le PDF de l'Explorateur est une synthèse, avec ses graphiques.** Une analyse ou un tableau
  de bord exporté en PDF (depuis la page ou par une étape `report` d'une tâche) se lit désormais
  comme les rapports de nomaubl : une couverture avec l'application, la période, la date,
  l'auteur et la version, puis les sections qui s'enchaînent (pas de sommaire, pas de page par
  section). Chaque graphique est dessiné côté serveur à partir des données de la section
  (`liberty/explorer/chart_svg.py` : barres groupées, empilées ou horizontales, courbes,
  camembert ou anneau) aux couleurs d'impression, avec légende, graduations compactes et les
  valeurs sur les barres — la même figure qu'un utilisateur exporte ou qu'une tâche rende un
  digest. Les KPI deviennent des cartes ; les nombres suivent la langue du lecteur (`1 234,5` /
  `1,234.5`), les mots du gabarit (libellés de couverture, mentions de confidentialité) aussi.
  Excel garde les données seules.
- **Toutes les chaînes de l'interface dans les deux langues (C10).** Les quelque 870 chaînes
  que les éditeurs récents ne portaient qu'en anglais en ligne (`t('clé', 'Défaut')` et options
  `defaultValue:`) vivent désormais dans `locales/en.ts` et `locales/fr.ts`, traduites ; le lint
  interdit un nouveau défaut en ligne, le fichier français ne peut plus manquer une clé en
  silence. Les questions de départ de l'assistant prennent un bloc de langue dans `ai.toml`
  (`[ai.<app>.l.fr]` avec `starters`, `groups`, `screens`), servi par `/ai/tools` dans la langue
  de la requête ; les trois applications livrent leurs questions en français.
- **Paramètres → Tableaux de bord indique quels widgets un filtre atteint.** Sous chaque filtre,
  l'éditeur liste les widgets dont l'écran porte la clé du dictionnaire ou dont le résultat a une
  colonne de ce nom (le bind de dictionnaire), et ceux qu'il manque.
- **Les tableaux de bord dans la langue du lecteur.** Un tableau de bord, un filtre et un
  widget acceptent un bloc `l` de surcharges par langue pour leurs textes — `l.fr = { label =
  "…", description = "…", sub = "…", count_label = "…" }` — y compris la colonne où une liste
  lit un texte (`title_column = "TITLE_FR"`), de sorte qu'une requête porte une colonne
  française à côté de l'anglaise. Les routes et l'export appliquent le bloc de la langue de la
  requête (`X-Liberty-Lang`, région retirée) ; le bloc lui-même ne circule pas. La note sous
  une section du PDF de l'Explorateur (période, filtres, groupes) suit aussi la langue. Les
  éditeurs de widget et de graphique montrent une colonne configurée dans l'orthographe de
  l'échantillon : un nom en majuscules dans le TOML retrouve la colonne en minuscules que
  Postgres renvoie.
- **Explorateur : les sélecteurs suivent le modèle.** Changer de modèle rapidement (le premier,
  puis celui de l'application courante) pouvait laisser les analyses enregistrées d'un autre
  modèle dans les sélecteurs : une réponse pour un modèle que la page a quitté est désormais
  ignorée, et une liste en échec vide les sélecteurs. Après un déploiement qui remplace le
  bundle, une page qui ne parvient pas à charger un morceau différé se recharge une fois au lieu
  d'afficher « Failed to fetch dynamically imported module ».
- **Les graphiques pivotent sur une colonne.** Une spec de graphique prend une colonne
  `series` : une série par valeur distincte (un processus, un statut…), chacune l'agrégat de
  la première colonne `y` — les conflits par rafraîchissement empilés par processus depuis une
  requête à trois colonnes, sans pivot SQL. Dans les éditeurs de graphiques sous « Pivoter
  par » ; l'export du tableau de bord dessine le même pivot. La boîte d'édition d'un widget
  édite les trois sortes de synthèse (chiffre, liste, barres) avec leurs colonnes, formats et
  un aperçu, et le sous-titre de chaque widget ; une carte du PDF montre sa ligne secondaire
  en petit sous le chiffre.
- **Des widgets de synthèse pour les tableaux de bord.** Trois sortes de widgets pour une page
  de synthèse comme celle de la facturation de nomaubl, en configuration : `stat` (une carte
  chiffre depuis la première ligne de la requête : la valeur formatée avec une unité, une ligne
  secondaire gabarit sur la ligne — `"{ENABLED:integer} actifs · {DISABLED:integer}
  désactivés"`), `list` (une liste de décisions ou un classement : titre, sous-titre, chiffre,
  compte, un point de tonalité ok / warn / error / info, une pastille) et `bars` (un classement
  avec une barre de proportion par ligne, part du total ou du plus grand). Chaque widget accepte
  un sous-titre `description`. Les cartes portent leur titre à l'intérieur ; l'export d'un tableau
  de bord montre un `stat` en carte, une liste ou des barres en table. L'éditeur visuel ajoute
  les trois sortes avec leur aperçu.
- **Un lien de menu ouvre l'Explorateur sur un modèle.** Un élément de menu `page` peut cibler
  `/explorer/<app>/<modèle>` : l'onglet ouvre l'Explorateur de données sur ce modèle (le
  sélecteur permet toujours d'en changer). Le bundle des applications s'en sert pour placer une
  entrée « Explorateur de données » dans chaque dossier métier (nomasx1 : Sécurité, Conflits,
  Licences ; nomajde : Sécurité, UDC, Développement).
- **Filtres et période d'un tableau de bord sur une requête sans écran.** Un filtre de tableau
  de bord (et la colonne de date de la période donnée comme élément du dictionnaire) ne se
  liait qu'à travers la carte `dd` de l'écran du widget ; un widget sur une requête nue
  l'ignorait. La page envoie désormais le choix comme *bind de dictionnaire* (`_dd_<ITEM>`,
  `_dd_<ITEM>_from` / `_to`) et le connecteur SQL le lie sur la colonne du résultat qui porte
  l'élément — trouvée comme le périmètre de données trouve ses colonnes : le `dd` d'un hint, le
  nom de la colonne, ou le nom moins le `dd_prefix` du connecteur (`ABCO` → `CO`), par une
  sonde unique. Un élément qu'aucune colonne ne porte est ignoré, comme avant. L'export fait
  de même.
- **Les tableaux de bord s'exportent en Excel et en PDF.** Un tableau de bord de configuration
  (`dashboards.toml`) a des boutons Excel et PDF dans sa barre, à côté d'Actualiser. L'export
  s'exécute côté serveur avec exactement ce que la barre contient — les filtres choisis liés par
  l'écran de chaque widget, les binds de la période et sa plage de dates, sous le périmètre de
  données de l'appelant (`POST /api/dashboards/{id}/export`) — et passe par les mêmes rendus que
  l'Explorateur : une feuille par widget (les KPI réunis en « Chiffres clés »), ou une synthèse PDF
  dont la couverture nomme la période et les filtres, avec les graphiques dessinés et les KPI en
  cartes. Un widget dont la requête échoue devient une section d'erreur ; le reste s'affiche.
- **Le volet de chat sur la mise en page de nomaubl** : une ligne d'en-tête (titre, choix du modèle
  s'il y en a un, « + » nouvelle conversation, ×), une ligne de contexte dessous quand la page en
  donne un, une bulle d'accueil, et les questions de départ en puces au-dessus de la saisie, sous
  des rubriques quand l'application les livre (`groups` d'`ai.toml`, par application ou par
  écran) ; les relances prennent la place des puces après une réponse. La carte d'indicateur du
  tableau de bord porte son libellé à l'intérieur, le chiffre aligné à gauche, le détail dessous,
  comme nomaubl.
- La ligne de tête du volet de chat ne garde que l'utile (le choix du modèle s'il y en a un,
  « nouvelle conversation ») ; un tableau dans une réponse défile latéralement en police réduite,
  chaque nombre sur une ligne. L'assistant connaît la date du jour (une ligne non mise en cache
  avec le contexte de page) : « le mois dernier » n'est plus deviné.
- **Puces de relance.** L'assistant termine chaque réponse par deux ou trois questions que
  l'utilisateur posera probablement ensuite ; le chat les affiche en puces sous la réponse (un
  clic en envoie une) et ne montre jamais la ligne repère écrite par le modèle.
- Le chat replie les appels d'outils d'un tour en une ligne discrète (« 3 appels d'outil », à
  ouvrir pour les voir) ; un appel en échec garde son message visible.
- Listes déroulantes : un code long (un identifiant de modèle) n'est plus tronqué dans la liste.
- Une requête triée sur une colonne qualifiée par un alias (`ORDER BY s.company_name`) échouait
  avec « missing FROM-clause entry » dès qu'un filtre ou un périmètre l'enveloppait : le ORDER BY
  remonté perd maintenant les qualificatifs.
- Périmètre de données : les valeurs d'une règle sont liées avec le type de l'élément du
  dictionnaire même quand la colonne revient en minuscules (Postgres) ; un code société issu d'un
  attribut utilisateur n'échoue plus avec « integer = character varying » sur un écran ou dans
  l'Explorateur.
- nomaflow `file_load` : avec `partition_by`, la requête de refus s'exécute une fois par partition
  présente dans le fichier, les colonnes de partition en binds (`:YEAR`, `:MONTH`…) : une garde de
  période clôturée suit le fichier plutôt qu'un paramètre saisi.
- nomaflow : l'erreur base de données d'une étape se lit comme la phrase du pilote au lieu de la
  classe d'exception, du préfixe de dialecte, de l'instruction et des valeurs liées. L'instruction
  va toujours dans le journal du serveur.
- Graphiques : l'axe Y affiche des graduations compactes (1,2 k, 27,5 M) dans une gouttière plus
  large ; une grande valeur était coupée à gauche (« 0000 » de « 20 000 000 »). L'info-bulle garde
  le nombre complet.
- Explorateur de données : un graphique dans une tuile de tableau de bord (et sur la page) restait
  un cadre vide quand la mise en page se stabilisait après la mesure de sa hauteur ; il se dessine
  maintenant à la hauteur mesurée.
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
