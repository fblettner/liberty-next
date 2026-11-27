# Liberty Next — Release notes

## 7.0.91 — unreleased

- **Licence usage report: an upgrade no longer looks like a new installation.** Where the accounts are kept in a file
  (the default), the report identified the installation by its host name — in a container, the container's id, which
  changes each time it is recreated. The licence tool therefore listed a new environment after every upgrade. The
  installation now keeps its own identity in a file next to the accounts file (`.installation`) and reports that. It
  changes one last time at this upgrade; the earlier lines can be deleted in the licence tool.

- **The Liberty overview opens faster.** Its integrity figures looked for unused queries by reading the whole
  configuration again for each query — over a second on a full installation with a fast processor, several on a small
  server. The configuration is now read once for all of them: about a tenth of a second for the same answer. *Find
  usages* and the Integrity page use the same reading and say exactly what they said before.

- **Lookups: a dropdown can hide an unreadable code.** A lookup whose value is a generated identifier (a JD Edwards
  task id) listed that identifier first, in a wide column, with the name cut off beside it. The lookup's new setting
  *Hide the code* (Dictionary → Lookups) lists the label and the extra columns only, in the label's order — in the
  record form, the grid's edit cells and the filters — and the grid starts with the code column hidden (it stays in
  the column chooser and in an export of all columns). The value saved is unchanged.

- **Menus: a page can name the screens it works through.** A page an application ships may read and save through a
  screen's own queries. Its menu entry can now list those screens (*Screens the page works through*, under Advanced):
  whoever is given the entry is given what the screens need — read, and change with the entry's change grant — the
  screens count as reached from the menu, *Find usages* lists the entry, and the integrity check says a screen that
  does not exist.

- **Application pages: a screen's record form can be opened from a page.** The page contract (now version 8) gains
  `sdk.Record`: a page adds or changes a record through a screen's own form — its fields, its rules per value, its
  lookups, its defaults, its save — without sending the user to the screen:
  `<Record app screen mode="add" values={…} onClose onSaved />`.

- **Integrity: a connector that serves, or that the tests call, is not "unused".** An API connector nothing of the
  configuration names was reported as unused even when one of its endpoints is served by a function or a shared action
  (it is there to be called from outside) or when a test suite calls it. Both now count as a use.

- **Application pages: the screens' import, an Excel workbook, the grid's own buttons** (page contract version 9).
  `sdk.Import` opens the data screens' import dialog from a page, for one screen or several (a workbook with a sheet
  per screen): same modes, same check against the rules then the database, each row saved through its screen.
  `sdk.workbook(name, sheets)` gives the user an Excel file of several sheets. `sdk.Grid` gains `toolbarEnd` (buttons
  at the end of its toolbar) and `noExport` (the page gives its own export).

- **After an upgrade, a browser no longer keeps the old application.** The application's entry page was served without
  telling the browser to ask for it again, so a browser could go on running the previous version against the upgraded
  server for hours — a new button doing nothing, or doing what it did before. The entry page is now always re-checked;
  the build's own files keep their caching.

## 7.0.90 — 2026-11-25

- **Pools: *Test connection* works on an LDAP directory.** The test answered *required argument is not an integer*
  before the directory was even asked: the waiting time was handed to the LDAP client as a decimal number, which it
  refuses. It is now given in whole seconds.

## 7.0.89 — 2026-11-24

- **Cards no longer disappear on a long page.** On a page listing many cards (Performance, a review, a dashboard), some browsers
  left cards as empty bands that came and went while scrolling. Each card carried a background blur, which made it a
  graphics layer of its own; the blur showed nothing on the page's plain background, so it is removed.

## 7.0.88 — 2026-11-24

- **A page that reads a lot opens at once: the server keeps its answer.** Some pages an application ships read a whole
  platform or every user and every rule before they can draw, and what they show changes when a collection runs, not
  while one looks. The function behind such a page may now be asked for its **kept answer**: what it answered before
  comes back at once, with the moment it was computed — which the page shows, so an old figure is never taken for the
  present — and the page's *Read again* computes anew, however long, and replaces it for everybody. A job prepares it:
  the step `liberty.kept:j_keep` (`function = "module:j_function"`, the function's parameters next to it,
  `langs = "en,fr"` for one answer per language) runs the function and keeps what it answers, so the first person to
  open the page after the night's collection does not wait. An answer is kept per function, parameters **and data
  scope**: what a user with a row-level scope computed is never given to another scope, and a job's answer goes to the
  users who have none. The permission on the function is checked before anything is read. For the applications' pages:
  `sdk.callKept(fn, params, refresh?)` (page contract version 7); for anything else, `kept: "use" | "refresh"` on
  `POST /api/plugins/run`. One table, `ly2_kept_answer`, created at start-up next to the accounts.
- A pool on Db2 or an IBM i is named `db2` / `ibmi` everywhere: once connected, the pool took the name IBM's driver
  gives itself (`ibm_db_sa`, `sqlalchemy_ibmi`), so a query written with a variant per database under `db2` or `ibmi`
  fell back to its default text. **Not run against a Db2 or an IBM i: checked on the names alone.**
- Monitoring → Direct: a pool's *Overflow* is never negative (a pool with fewer connections open than its size showed
  `-4/0`).
- Settings → Pools: the kinds that are not a product's name (*LDAP directory*, *HTTP server*) are in French too.

## 7.0.87 — 2026-11-23

- **A pool may point at SQL Server, Db2 and an IBM i (AS/400).** Liberty opened its pools through drivers that speak
  asyncio only, which neither IBM's Db2 driver nor the plain SQL Server one does. A pool whose driver does not is now
  run in threads behind the same interface — what the *thick* Oracle pools already ran on — so queries, screens, jobs
  and the applications read it like any other pool; a driver that speaks both ways (Oracle's, PostgreSQL's) keeps its
  asyncio path. The image carries the SQL Server driver, and the Db2 one on amd64 only (IBM publishes none for arm64 Linux); elsewhere they are optional: `pip install 'liberty-next[mssql]'` (SQL Server,
  `mssql+pymssql://` — nothing else to install), `liberty-next[db2]` (Db2 for Linux / UNIX / Windows, `db2+ibm_db://`
  — IBM's driver has no wheel for arm64 Linux), `liberty-next[ibmi]` (Db2 for i, `ibmi://` — through ODBC: it also
  needs unixODBC and IBM's *IBM i Access ODBC Driver* on the server). A pool whose driver is missing says which of the
  three to install instead of a bare import error. Checked on a SQL Server 2022 and a Db2 12.1 run locally; **no IBM i
  was at hand: the `ibmi` path is not tested.**
- The image always takes the system packages' latest security fixes: the build step that upgrades them was kept from the
  build cache, so a fix Debian published after that cache was made was missing (the release's image scan refused it).
- Settings → Pools: *SQL Server* starts from the driver that needs nothing installed (`mssql+pymssql`; the ODBC one is
  still offered), and *Db2* and *Db2 for i (IBM i, AS/400)* are kinds of database of their own, with their usual port.
  *Test connection* asks Db2 the one statement it accepts (`SELECT 1` has no meaning there).

## 7.0.86 — 2026-11-23

- **Docker install: the administrator's password is given whenever the install creates the account.** The end of
  `./install.sh` showed the password only when that same run had written the `.env`. With an `.env` already there and
  no account yet — `./install.sh prepare` first, a first run stopped before the stack started, data volumes removed by
  hand — it said *password unchanged from the original install*, while the server had just created `admin` with a
  password of its own, written only in the container's log (and lost when the applications' install recreates the
  container). The install now reads what the server did: an account created by this run is shown with its password,
  whoever chose it; an account that was already there is said to be so, with the command that sets a new password —
  and a password is no longer shown for an account this run did not create.
- **Opening the application no longer waits for the fonts.** The page asked Google Fonts for its fonts in a way that
  held everything — nothing drawn, the application not started — until the answer. On a network that lets the browser
  reach Liberty but not the internet, and stays silent instead of refusing, the page stayed blank for as long as the
  browser waited, at each opening: measured, 15 s of silence was 15 s before the sign-in page. The fonts are now asked
  for beside the page: it shows at once in the system font and takes its own when — and if — it arrives.

## 7.0.85 — 2026-11-22

- **PostgreSQL: Liberty's own connections no longer compile their queries (JIT).** PostgreSQL compiles a query it
  believes heavy before running it, at every run. On the short queries of screens, searches and dashboards the
  estimate is often wrong and the compilation then costs far more than the query: measured on a search, 145 ms of
  compilation for 4 ms of work — and seconds on a server with one processor. Each PostgreSQL pool now turns it off for
  its own sessions; nothing changes for the database's other users. A pool that wants the database's own setting says
  so (Pools → *Tuning* → *Let PostgreSQL compile queries*). The collections and the jobs run on the same pools: the
  heavy reads measured ran as fast or faster without it.

- **Upgrade of the applications: a menu keeps the shipped order.** A new menu entry was put at the end of its menu,
  and an order that was shipped again was ignored. When nobody moved an entry on the platform, the menu now takes the
  shipment's order: a new entry lands where it was put, the customer's own entries stay after the entry they followed.
  When the operator moved entries, his order is kept as before, and a new entry goes in its folder, after the entry
  it follows in the shipment. `--force-config` takes the shipped order as it takes the rest. The merge report says
  which menus were put in order.

- **Monitoring → Live: no table is cut any more.** The four panels (database pools, connected users, record locks,
  runtime) shared the width in equal parts, and the pools table — five columns — was wider than its quarter: its last
  column was cut. Each panel now takes the room its content needs and they wrap when there is not enough; a pool not
  opened yet says so on one line; a table that is still too wide for a narrow window scrolls inside its panel.

## 7.0.84 — 2026-11-22

- **Configuring without knowing the file behind it** — what a consultant met when building an application:
  - **A configuration that cannot be saved says which object and which field**, in the builders' own words
    (*demo › Screen "Orders" › Column "B" › Kind of field: must be text*), with **Open** to go there; a refused
    save no longer closes the designer or replaces it by the message. Jobs and menus are checked before they
    are sent (a step without what it acts on, an entry that opens nothing, an id used twice).
  - **The generated forms have plain labels** ("Dictionary item", "Kind of field", "History table") and one
    short sentence of help, the longer explanation behind *more*.
  - **A column's *Kind of field* is one choice in plain words** ("A yes / no box", "A list read from a table",
    "A number given automatically"…) with only the field that kind needs; *a list read from a table* is made
    in one step from the column.
  - **Settings that had no field now have one**: a menu entry's fixed values, the application's name, an API
    connector's home per role and licence, a service's questions and its inputs' labels, a dashboard filter's
    opening value, chart options, an Explorer entity read from a table, a model's heading and what it delivers,
    a step's timeout / default / environment, a job's log level, presets (rename, change, delete), a rule's
    action values, a report's formats, PDF settings and parameter choices, a test suite's name, a post-apply
    step's values, a screen's own assistant headings.
  - **Editors name things by their label**; a new screen, dashboard or service is given a name and its id is
    made from it; step kinds, modes and events are said in words; the role panel saves from the page header and
    asks before unsaved changes are lost.
  - **Jobs: tables, time zone and recipients are picked**, and a schedule is said in words (every N minutes or
    hours, chosen days at a time, once a month). **Services: a step picks its values** from the inputs and from
    what earlier steps returned.

- **A record opened by clicking a row is the one clicked**: its key is matched exactly. It could open — and
  save — another record whose key contained the first.

- **Jobs: saving a job no longer fails when another job of the file carries shared step settings.** **Reports:
  a template with a parameter can be saved** (it answered an error).

- **Jobs: a *notify* step sends to the inbox and by e-mail only.** It also accepted an "action" channel that did
  nothing there (it is a notification rule's: it runs the rule's query or endpoint). A job that must call an
  endpoint has its own step for it.

- **Tests of an application: a first case from an endpoint.** *Case from an endpoint* on the Tests page asks
  which endpoint and prepares the case: a step calling it with the example values of its variables, expecting
  what it answers now (at least one row, or the values it holds). An endpoint that changes data is not called:
  its case comes marked as writing. Nothing is saved until you save the suite.

- **Unsaved changes are no longer lost without a word.** A page that holds edits not yet saved — every Settings
  builder and editor window, a job being edited, a data grid in bulk edit, an open record form — now tells the
  shell, which asks before what would lose them: **closing the tab**, **closing every tab**, **switching the
  language** (every open page is drawn again), and the **browser** itself asks before a reload or a closed
  window. Nothing is asked when nothing is unsaved. **Moving to another page asks nothing either, because
  nothing is lost: the page keeps its tab** — also in single-tab mode (`[ui] tabs_multi = false`, where the
  page opened used to replace it) and when the tab cap is reached (the oldest tab with nothing unsaved closes
  instead); an application switch that closes tabs leaves it open too. **A tab that holds unsaved changes
  carries an orange dot**, so the page to come back to is visible. **The job editor is a tab too** (one per job
  edited, *Job · <id>*): going to the list and coming back keeps what was typed, and its *Back* button asks
  before it drops unsaved changes (it used to drop them without a word).
- **A page an application ships tells the assistant what the user is looking at** — the page contract
  (version 4) gains `useAssistantContext(title, facts)`: a title and a few facts in a few words (the object
  open, the tab, a filter — never rows; blank facts are left out, long ones cut, 20 at most). The chat shows
  it as its usual chip, sends it with each question while that page is on screen, and picks the starters the
  application declares for the page (`[ai.<app>.screens.<page>]` in `ai.toml`). So "where is this used?" needs
  no name on a page any more than on a screen.
- **Found by the documentation review, fixed.** The endpoint setting `empty_on` (the refusals that mean "no
  rows") is edited in the endpoint editor, with the other row settings. An Explorer entity on an API endpoint
  with rows is picked in the entity form (the API connectors that have one are listed beside the SQL ones).
  The Studio no longer says "nothing opens" of a screen an Explorer entity opens. The Authentication card of an
  API connector names *Each user as himself* and says when its signing key is missing; `{USER}` is in the
  notify step's list of placeholders.
- **Wording.** What the menu calls a *Service* is called so everywhere (the designer's title, "Served by a
  service" on an endpoint, the package and dependency lists). Strings that stayed in English in a French
  interface are translated: the job states in every list, the retention summary, relative times ("il y a 4
  min", from the interface's language), the Live page's Runtime card, the dependency kinds, a data-scope rule's
  summary, a test run's status, the shared form's own words (add, default, yes / no, the SQL field's
  variants). The hints no longer speak of v1, v2 or internal phases; the hint of a condition says what it
  tests whatever the condition; the events hint points to the Actions stage. French terms aligned (*Concepteur
  d'écran*, *périmètre*, *Modèle du corps*, *point d'accès*, *Paquets de modifications*, the report templates).
  **The sign-in page offers single sign-on only where it is set up**, with a hint meant for the user. An API
  connector's password, token and signing key have the show / hide button the pool's password has.
- **A screen, its tabs and its actions in the user's language.** A screen gains `l` (its label per language:
  the tab, the list of screens) and `description_l` (its heading); an action gains `l` (a row-menu entry, a
  toolbar or tab button, a step of a chain). The server sends each of them already in the language of the
  request (`fr-FR` falls back on `fr`). All three are entered in the designer: the screen's on its first stage
  (*What it is*, under the label and under the description), an action's on its *Translations* tab, a tab's in
  the form builder's *Tab settings* — with the two flags that leave a tab out when a record is added or edited.
  **A dialog tab's translations, which existed in the configuration but were never applied, now are.**
- **Every text given in another language is entered in its builder — none is left to the file.** Three were
  not. **Dashboards**: a dashboard's title and description, a card's texts (label, subtitle, sub line, unit,
  count label, note, a text card's Markdown, and the column to read where a query returns one per language),
  a link's label and description and a filter's label each show an *In the other languages* block under the
  field — a blank there shows the text as written. **Saving a dashboard from the builder dropped its title and
  description in the other languages, and a links card its links': they are kept.** **The assistant's starter
  questions** (Liberty → AI): a *Language* picker shows the questions as written or as said in a language (a
  list left empty there falls back on the one as written), for the application, its screens — and its
  **headings**, the grouped questions the chat shows first, which had no editor at all. The block is one shared
  component (`common/LocalizedTexts`, logic in `services/localized.ts`), the picker's logic is
  `services/aiStarters.ts`; both tested.
- **Data Explorer: a saved analysis or dashboard is found whatever its model.** An object is saved under one
  model and the page listed those of the model shown: one saved elsewhere was found by trying each model.
  **Find**, beside *Analyses / Dashboards*, lists every saved object of the models the user may use — the
  shared ones and his own — under its application and model; typing narrows it (name, description, audience,
  owner, model; case and accents ignored), and picking a line opens it, switching to its model when it is
  another. The model picker says what each model holds ("8 analyses · 1 dashboard"). One route behind it,
  `GET /api/explorer/saved` (no definition in the list: opening reads it).
- **The server of an API connector is a pool too.** A pool whose URL starts with `http://` or `https://` is an
  HTTP server (kind *HTTP server* in Liberty → Pools): its address with its account, its password — or its
  token when the server takes one —, the key that signs a token per user and its certificate, the TLS check.
  An API connector names it with **Server** (`server`), and so does each of its environments; the base URL
  and the credential fields then leave the connector, which keeps HOW it authenticates and its endpoints. What
  belongs to the installation is in one place, and a connector an application ships holds none of it: an
  upgrade refreshes it whatever the address (a literal address changed on the connector made it "modified",
  kept as it was). A connector that names no server works as before. A pool that is missing, or shipped
  without its address, does not stop the installation: the call says what to set. *Test connection* on such
  a pool says the server answers; the connector's own Test checks the account.
- **An LDAP directory is a pool — one place for every connection.** A pool whose URL starts with `ldap://` or
  `ldaps://` is a directory: created under Liberty → Pools like a database (kind *LDAP directory*: host, port,
  base DN; its account typed as it is — a bind DN, `DOMAIN\user`, `user@domain`; its password kept like any
  pool's, `ENC:` or `${ENV}`), and *Test connection* binds once. Whatever reads a directory names the pool
  instead of carrying a server and an account: the `ldap_sync` step gains **Directory pool** (its own server /
  bind DN / password stay for the jobs that have them), and an application's code asks
  `connectors.pools.directory(name)`. A directory pool runs no SQL and says so by name; a database pool asked
  for a directory does too (`liberty/connectors/directory.py`). `connectors.pools.where(name)` says where any
  pool points — host, port, database or base — without its account: what a report prints of a system.
- **A field can offer the installation's pools.** New field rule **POOL** (dictionary entry or screen column):
  the field is a dropdown of the pools — every pool, or those a connector may run on when the rule names one —
  wherever an enumeration is drawn (grid, record form, filter). A setting that names a pool no longer has to
  be typed.
- **A job's run parameters are edited in the job editor.** What a user may set when starting a job from its
  menu page (`user_params`) was written by hand in `jobs.toml`; the editor now has *What the user may set when
  starting it*: per parameter its name, its label and the label in the other languages, the step it goes to,
  its choices (typed freely, a fixed list, the rows of a query, an application's Explorer models, the test
  suites), a default, required, several values — and **One run per value of** (`for_each`). The job's
  **timeout** is there too, with its **position in the installation** (`install_step`) and **Keep this job as
  it is on upgrade** (`override`): every setting of a job now has its field. Saving writes exactly what was loaded plus the change (`services/jobParams.ts`,
  tested).
- **Studio and builders, corrected.** The Studio says of a screen "nothing opens it" by Integrity's own rule (a
  row-menu drill, a tab of another form and an Explorer entity count, as there); a dashboard an entry opens is
  no longer tagged "not in the menu"; *Edit the menu* opens on the application shown; the Menu column counts
  entries, as the overview does (folders are not entries). **A menu entry's fixed parameters are passed** when
  a screen, a query or an endpoint opens from it, and the entry type *Link* (`url`) is offered in the editor.
  *Generate from DB* no longer shows "couldn't introspect" while it waits for a table filter. The report step
  of a job lists its periods by name, the notification rules name their channels and recipient kinds, the
  screen assistant's steps are translated. A connector's line counts queries, sequences and lookups apart.
- **Integrity: a screen an Explorer entity opens is not an orphan.** An Explorer entity that names a screen
  opens a detail row in that screen's dialog: the screen is reached even when no menu leads to it.
- **A page an application ships: a row of its grid can be opened** — the page contract (version 3) gives `Grid`
  a `detail` function: every row gets a chevron, and what the function returns is drawn under the row (an
  object's dependencies, a nested list).
- **An API connector can make each call as the signed-in user** — a new kind of authentication, *Each user as
  himself (a signed token)* (`auth_type = "user_jwt"`), beside the service account. Liberty signs a short-lived
  JSON Web Token that names the user on the server and sends it as `Authorization: Bearer`; the server trusts
  the certificate of Liberty's key, so **no password of the user is kept anywhere**. Made for JD Edwards AIS
  (the HTML server's external JWT trust), usable with any server that accepts a signed token. Who the user is on
  the server comes from a user attribute (`jde_user` by default, set by an administrator; blank = the Liberty
  name). Settings, all in the connector editor: the signing key (a PEM file's path or the PEM text, `ENC:`
  allowed), the attribute, the field of the token that carries the user (`sub`), the signature (RS256), issuer,
  audience, validity (300 s), the optional certificate whose fingerprint goes in the token (`x5t`), and what to
  do when no user can be named — a job, a user without the attribute:
  refuse the call (the default), or use the service account. It is a choice per connector: nothing changes for
  a connector left on its service account.
- nomaflow — **a job sends its result to whoever started it; a file's numbers are read as the file writes them.**
  - A **notify** step's recipient can be `user:{USER}`: the person who started the run ("send me the export I
    asked for"). A run nobody started (a schedule) leaves that recipient out, and a step left with no recipient
    sends nothing and says so, without failing. `{USER}` also fills the subject and the body.
  - A **file load** step has a new setting, *Numbers written as text* (`decimal_separator`): `.` (1,234.56) or
    `,` (1.234,56) names the file's decimal separator, the other one being a thousands separator. The default,
    *detect*, now reads what is not ambiguous — `1.234,56` and `1,234.56` both give 1234.56 (the first gave
    1.23456), and `1,000` in a WHOLE-number column gives 1000 (it gave 1) — and still reads a lone `1,000` in a
    decimal column as 1: name the separator when a file writes thousands that way. Typed spreadsheet cells are
    unchanged.
- **Liberty Next has a license: the Business Source License 1.1** (`LICENSE`, `NOTICE`). Source-available,
  not Open Source: free to read, copy, modify and fork, and to run in production for your own organisation and
  your own applications — the applications, configuration and plugins you build on it are yours to sell.
  Selling or reselling Liberty Next itself (or a modified version), or offering it as a paid hosted service,
  needs Nomana-IT's written agreement. Each version becomes available under the GNU AGPL v3 or later four
  years after its release. It applies from this version; earlier versions were published without a license.
  Nomana-IT's applications (nomasx1, nomajde…) are separate products under their own terms. Contributions
  come through forks and pull requests, under a contributor agreement (`CONTRIBUTING.md`, `CLA.md`).
- **Security — API connectors: a parameter can no longer redirect an endpoint.** The `{{placeholders}}` of an
  endpoint were filled as plain text, in the path and in the body. A caller holding the permission on one
  endpoint could therefore reach another path of the same connector (a value such as `../other/service`), or
  add fields to a JSON body (a value holding a quote) — so the permission `api:<connector>:<endpoint>` was in
  practice a permission on the whole connector. Now:
  - in a **path**, a value may add segments (`report/execute`) but a `.` / `..` segment is refused — written
    plainly, percent-encoded or with a backslash — `?` and `#` are encoded, and a value cannot give the
    address of the call; the call is refused before anything is sent (not even a token request);
  - in a **JSON body**, a value placed inside a string (`"name": "{{NAME}}"`) is inserted as string content
    and cannot end that string. A value a caller had already escaped is kept as it is; one it had not (a
    Windows path, a line break, a quote) is now escaped instead of producing an invalid body. A placeholder
    written outside quotes (`{{BODY}}`, a number) is still inserted as is: that is the author's choice;
  - in a **form-encoded body**, a value of a `name=value` pair is form-encoded and cannot add a pair
    (`&admin=1`); one already encoded is kept; a placeholder standing alone (`{{BODY}}`) is inserted as is;
  - the **file of a multipart part** (`file=@…`): when the values come from a user — the HTTP route, a screen
    action, the assistant, a notification rule, a replayed change — the endpoint must name the folder
    (`@/data/out/{{NAME}}`) and the value stays inside it; `@{{FILE}}` is then refused, as the permission on
    the endpoint would let its holder send any file of the server. A job or a plugin still gives the path it
    wants. A file part that cannot be read is now a failed call with its reason, not an error 500;
  - a value is no longer read again for placeholders (a value holding `{{token}}` stays that text).
  Existing endpoints keep working: only calls that left their path, produced an invalid body, or let a user
  choose the file to send, change.

- **API connectors: a parameter says what it expects.** A parameter of an endpoint (or of a query) had a name, a
  label and a default. It now also has a **help** text and an **example**, edited on a second line under each
  parameter in the endpoint editor, shown under the fields in the HTTP runner and in the editor's test pane,
  and given to the AI assistant with the endpoint. A JSON parameter no longer has to be guessed.
  A parameter with only a few possible values lists them (**allowed values**, a third field on that line): it
  is then a dropdown where it is filled, and an endpoint checks it — another value is refused before anything
  is sent, and the letter case is corrected (`VIEW` → `view`).
  A declared parameter the caller leaves out, or sends blank, now takes its default (blank without one); it
  used to stay in the request as the text `{{NAME}}`, or to replace the default with nothing.
  The connector's endpoints are listed in alphabetical order, in the Endpoints tab — with a search box (name,
  label, path, method, description) once there are more than five — and in the Test tab.

- **API connectors: create an endpoint from what the server describes.** A generic endpoint ("run any report",
  with JSON for whatever depends on the report) is hard to fill. A connector can now declare **generators**
  (`[[connectors.<name>.generators]]`: an id, a label, a plugin function): the Endpoints tab then has a
  **Create from…** button — search the object, answer a few questions (which columns, which options), and the
  endpoint is written with one named variable per input, its label, its help and its default. It is added
  unsaved and opened, for you to review and rename before saving; it has its own permission
  `api:<connector>:<endpoint>`, so a role can be allowed one report or one orchestration, not all of them.
  The generator is the application's (a `j_` function under plugins/, called in three steps: `search`,
  `options`, `build`); the framework runs it against the saved connector (`POST /admin/config/api/generate`,
  superuser) and validates the endpoint it built. The connector's generators are edited from the Endpoints
  tab.
  A generated endpoint remembers what it was created from: **Refresh from the server**, on the endpoint,
  builds it again and shows what changes before applying it — the address, the body, which parameters exist
  follow the server; your name, label, description and what you wrote on a parameter that stays are kept.
  **Check generated** asks the server for every generated endpoint and lists those that no longer match. The
  integrity checks warn about a generated endpoint whose generator the connector no longer declares.

- **AI assistant: an API endpoint that changes data is not called like one that reads.** With the API tool on
  (`[ai] api_tool`), `api_call` called any endpoint the user was allowed — a report submitted, an orchestration
  run — with no card, without `ai:write`, and over MCP without `[ai] mcp_changes`. An endpoint now says it
  **changes data** (a checkbox in the endpoint editor, `writable = true`; a "changes data" tag in the list):
  - `api_call` refuses it and lists it as `changes_data`;
  - a new tool, **`api_run`**, calls it — offered when both `[ai] api_tool` and `[ai] write_tools` are on, to
    a user holding `ai:write` and the endpoint's own `api:<connector>:<endpoint>`. In the Liberty chat it
    checks everything (the endpoint, the permission, each value) and proposes a **card**; nothing is sent until
    the user applies it (`POST /ai/api-run`). Over MCP it makes the call, and needs `[ai] mcp_changes`;
  - the call is in the usage record like the other changes made through the assistant.
  An endpoint not marked is treated as a read, as before: **mark the endpoints that write** on your API
  connectors.

- **API connectors: a server that answers 200 with its errors inside.** Some servers report a refusal in the
  body of a successful answer (a JD Edwards form that refuses a save answers 200 with an `errors` list).
  The call was then a success for Liberty: the test pane said so, an action went on to its next step. An
  endpoint now names its **error fields** (dot-paths, in the endpoint editor): when one of them holds
  something, the call is a failure and its error is that content ("Alpha Name Cannot Be Blank"). The whole
  answer is still returned. **Warning fields** work the same way without failing the call: their content is
  reported with the answer (`warnings`), in the HTTP runner, the test pane and to the assistant. A path may end
  with a filter on a list's items, `result.errors[errorLevel=1]`, for a server that puts its errors and its
  warnings in one list.

- **A screen can read an API endpoint.** A screen read a named query of an SQL connector, nothing else; the
  answer of an endpoint was JSON in the HTTP runner. An endpoint whose answer holds a list of records now says
  where it is — **Rows** in the endpoint editor (`rows_field`), with the records' keys mapped to column names
  (`row_map`), the place of the columns' titles (`labels_field`) and of the server's "there is more" flag
  (`more_field`) — and a screen names it as its read: `connector` = the API connector, `read_query` = the
  endpoint. The grid is unchanged: the same address serves both, with the same columns, labels from the
  screen then the dictionary then the server's own titles.
  What differs from a query, by design: the screen's **filters are the endpoint's parameters** (a filter on a
  column the endpoint has no parameter for is not offered; the filter panel's operators are not translated);
  the **row limit is the endpoint's**, capped again by the screen, without paging — the screen says the result
  is truncated when the server says there is more; an endpoint can name the parameter that carries its limit
  (**Row limit parameter**, `limit_param`): the screen then fills it from its own "Max rows" and no longer
  shows it as an input; the other parameters stay above the grid (their example as a placeholder, their help
  as a tooltip) unless a filter column of the same name puts them in the filter panel; a value picked from a
  lookup is sent without its blank padding; sorting and grouping happen in the browser on what was
  returned; the caller's **data scope** applies to the rows returned, each rule with its own mode as for a
  query. The permission is the endpoint's (`api:<connector>:<endpoint>`), in the grid's route, the screen
  catalog and the menus.
  **Saving through endpoints.** The screen's update, insert and delete queries can name endpoints marked
  "Changes data": the grid and the record form save as they always do, and the row's values go to the
  endpoint's variables by name (the value a row held when it was loaded under `<COLUMN>_ORIGINAL`, as for a
  query). The endpoint's permission is needed; the screen's `editable_when` is enforced; what the server
  refuses (its error fields) is the save's error, in the server's words; its warnings come back with the
  answer; a value the endpoint maps out of its answer (`response_map`) returns to the form, like a number a
  sequence assigns; the notification rules fire. Checked by the integrity report. The assistant reads such a
  screen's rows with `sql_query` (the endpoint's parameters are its only filters; the sensitive columns are
  left out as for a query), and its write tools go through the same save.
  Not on an API screen: the import of rows, the configured workbook export (the grid's own export works),
  the server-side summary, the audit mirror, change packages on its saves, the dictionary's write rules
  (SEQUENCE, LOGIN, SYSDATE — the server decides), the Data Explorer.
  Two things a save through an API needs, both there: **a screen's own parameters go with every save** (the
  values it was read with, under the row's own — the header of a detail form: the product code and the type a
  row of codes belongs to; in the grid, the record form and the server-side save), and **a parameter can be
  required** (the endpoint editor's "Required"): a call without it is refused before anything is sent, instead
  of the server taking a blank and acting on it.
- API connectors — **a blank query parameter can be left out.** An endpoint whose optional filters travel in
  the query string sent them empty (`?instanceName=`) when left blank, which some servers refuse or read as a
  filter on nothing. The endpoint editor's "Leave out a query parameter whose value is blank"
  (`skip_blank_query`) sends only the ones that have a value. Off by default: existing endpoints are
  unchanged.
- **The application on a phone and a tablet.** Below 768 px: the menu is a drawer opened by a button at the top
  left, the header's buttons sit in a row of their own above the tabs (which scroll sideways), every dialog is
  full-screen, a page's margins shrink — and **a grid is shown as a list**: each row a card, each column a
  "label · value" line, the search, the pager, the row menu and Bulk edit still there (sorting and the header
  filters are a wide-screen gesture). A record form shows its fields in one column, its tabs scroll. Above
  768 px nothing changes for a mouse; the builders and the Explorer's authoring are still desktop layouts.
- On a phone, **a row's card has a heading**: the first two columns side by side, larger, then four lines, and
  "+N" opens the rest — blank values give no line, and a field shown as a code and its label is ONE line (the
  label to read, its picker in Bulk edit). A column says otherwise with its new **Card** setting (`card`:
  `title` = in the heading, `show` = always a line, `hide` = never). The filter panel's fields take the whole
  width, the period picker shows its presets as a row above one month, a field no longer makes iOS zoom the page.
- **On a tablet in portrait** (768 to 1100 px) the menu is the icon rail whatever the saved mode, opened over the
  page by its button (a finger does not hover) and folded back by a tap elsewhere; the header's buttons take
  their short form so the tabs keep their room.
- **The menu is as wide as you make it**: drag its right edge (180 to 460 px, remembered on this browser; a
  double click gives the default back). And **a menu entry's label takes two lines** instead of being cut
  after a few words (its full text stays the tooltip). The page of a screen that reads an API endpoint is titled by the screen's label, not by the
  endpoint's description (a sentence).
- A screen on an API endpoint — **its advanced filters work on every column it flags.** A filter was offered
  only where the endpoint had a parameter of that name; any other flagged column is now filtered on the rows
  the endpoint returned, with the same operators as a query's (contains, equals, not equals, starts / ends
  with, a range) — and a column with an enum or a lookup is a pick-list there, a lookup being free to read an
  endpoint itself. A **parameter with a closed list of values** (`choices`) is a dropdown above the grid
  instead of a text field. (An endpoint whose server caps the rows is filtered within what it returned.)
- API connectors — **a parameter called `username` or `password` is the endpoint's, not the connector's
  login.** `{{username}}` / `{{password}}` fill a token request with the connector's own credentials; an
  endpoint that DECLARED a parameter of that name got them too when it was left blank — a user filter was sent
  as the connector's login (and answered "no such user"), and a password field left empty would have carried
  the connector's password. A declared parameter left blank is now blank.
- API connectors — **a server that refuses when it has nothing to list.** An endpoint's new "empty on" setting
  (`empty_on`) names the refusals that mean "no rows" — part of the server's error ("no active users"), or its
  HTTP status (`404`): a screen then shows an empty list instead of an error. An HTTP error now carries **the
  server's own words** when the endpoint's error fields find them ("HTTP 500 — There are no active users…",
  not a bare "HTTP 500"). And a list that is **not where the endpoint says** (`rows_field`) is an error naming
  what the answer holds, instead of a screen that stays empty for no visible reason.
- An API server that cannot be reached is **said with its address**: "cannot connect to https://host:8999 —
  check the connector's base URL", where the driver's own text ("nodename nor servname provided") named neither.
- A PDF asked on a machine where **the Pango library is missing** (WeasyPrint installed, `brew install pango`
  not done) answers 503 with what to install, instead of a 500 and a traceback in the log.
- **A screen read by a query can save through another connector's endpoints** — the screen's **Saves through**
  (`save_connector`, Screens → Queries). The screen keeps its query, its columns, its form; its update / insert /
  delete name endpoints of an API connector, so the save goes through the application that owns the data (a
  JD Edwards form: JDE's own rules, defaults and messages apply) while the read stays fast. An endpoint's
  variable takes the value of the column of its name, else of the column carrying that dictionary item — its
  `dd`, or its name minus the connector's `dd_prefix` (`ALPH` ← `ABALPH`): no bind list to write. Everything a
  screen's save has still holds: the endpoint's permission, `editable_when`, the notification rules, the grid
  and the form unchanged, the assistant's `screen_write`. Integrity, find usages, rename and packages follow
  the save connector. The save hooks (`on_update`…) were not the place for this: they run after a save the
  screen must already be able to do.
- A save through an endpoint — **"not given" is not "blank".** A column the screen does not send leaves the
  endpoint's variable out (a function serving the endpoint receives `None` there and leaves the field alone);
  a column sent blank is given blank — the user cleared it. Before, both arrived blank, and a field the screen
  did not read would have been blanked. And on a **change** served by a function, a column that still holds what
  the row was loaded with is left out too: only what the user changed is typed (a form re-checks a field typed
  again — JDE warned of a duplicate name on every save). A save also **names its screen** (`?_screen=&_app=`), so two screens
  saving through one endpoint each keep their own rules and columns.
- "Create from…" — **a generator may build several endpoints at once** without a screen (`{endpoints: […]}`): the
  editor adds them all to the list.
- "Create from…" → a screen — **endpoints that already exist** are no longer a refusal: they are kept as they
  are (the new screen uses them), or, on request, written again in place (`existing_endpoints` on the scaffold
  route: `refuse` by default, `keep`, `replace`).
- API connectors — **endpoints under a heading.** An endpoint has a **Group** (`group`): the connector editor
  lists the endpoints of one group together under that heading, which folds, with their number; the search
  also looks in it. For a connector holding the endpoints of several applications or subjects. Display only;
  an endpoint without a group is listed first, as before.
- **A generator can build a whole screen.** "Create from…" wrote one endpoint. A connector's generator may now
  answer with a screen: the endpoints it reads and saves through, and the screen itself. The dialog then asks
  for the application, the screen's id and title and whether to add it to the menu, and creates everything in
  one validated pass (the Screen Assistant's route, `POST /admin/assistant/scaffold`, which now takes
  `endpoints`): nothing is written when anything is invalid or an endpoint's name is taken. Each endpoint keeps
  what it was built from, so "Refresh from the server" works on it. In the endpoint list an endpoint served by
  a function shows `FN` instead of a method.
- **An endpoint served by a function.** One HTTP call cannot do everything: changing a row a server only
  exposes through a form means finding it, checking that exactly one was found, then acting — several calls in
  one session. An endpoint can now name a function of an application's plugin (**Served by a function**,
  `callable`) that answers instead of an HTTP call. Its arguments are the endpoint's parameters, or the JSON
  object the body gives once its `{{placeholders}}` are filled (so fixed arguments and variables mix, and a
  user's value stays inside its string); its answer is treated like a response — rows, error and warning
  fields, response field and map, "Changes data" — so a screen, an action, a job or the assistant use it like
  any endpoint. Only a function discovered under `plugins/` (`j_*`) can be named; an exception is the call's
  failure; the integrity report names an endpoint whose function is not installed.
- **A long text keeps its lines.** A column whose format is Long Text (`textarea`) was typed in a one-line
  box, in the record form and in the grid's edit mode: its line breaks were not shown, and lost as soon as
  the value was edited. It is now a multi-line box in both (the form's grows with the text).
- **A row action says what it did.** The message of a `notify` step in a row's menu action was only written
  to the browser console; it is now shown in the grid's status banner, as a toolbar action's is.
- **A field of `name = value` lines, shown as fields.** A new format, **Name = value list** (`pairs`): in the
  record form the text is a list of name / value rows (add, remove), with the text itself one click away. With
  the column hint **`params_of`** — the column of the screen that names an endpoint of the screen's connector —
  it becomes that endpoint's own form: one labelled field per declared parameter, its help under it, a dropdown
  for its choices, a required mark; a name the endpoint does not declare is still shown. For any screen that
  stores the parameters of a call someone must fill in.
- A record form opened from another record form (a nested table's row) **no longer overflows it**: when its
  content is taller than the form behind, it scrolls — its title and its Save button stay in view.
- `liberty.connectors.api_rows.answer_rows(endpoint, answer)`: the rows of an endpoint's answer as a screen reads
  them (`rows_field`, `row_map`), for an application's code that checks an answer.
- **An endpoint served by a shared action — steps composed into one call.** An API endpoint can now name a
  shared action (**Served by an action**, `action`; Settings → Actions is where it is built): its steps —
  queries, other endpoints, plugin functions, conditions, loops — run on the server and answer as ONE endpoint,
  which a screen, an action, a job, the assistant or an outside caller uses like any other. The endpoint's
  parameters (or the JSON object its body gives once filled) are the action's inputs (`INPUT.<name>`; one not
  given takes the action's own default); what the action `return`s is the answer, its `notify` messages come
  as `messages`; a step that fails is the endpoint's error. **It runs with the endpoint's permission**: who
  holds `api:<connector>:<endpoint>` runs its steps, which are not checked again against the caller — a
  composed service is granted as a whole, as an orchestration is. The caller's row-level data scope still
  applies to every query. An action that calls the endpoint it serves is refused; the integrity report names
  an endpoint whose action is not defined; "find usages" of a shared action lists the endpoints it serves and
  renaming it follows them. In the endpoint list such an endpoint shows `ACT`.
- **A step reads an endpoint's rows by column name.** When a `call_api` step (in a screen action or a shared
  action) calls an endpoint a screen can read — one that says where its rows are (`rows_field`) and names its
  columns (`row_map`) — what it binds is those rows: the next step reads `<step>.first_row.<COLUMN>` and
  `<step>.rows`, instead of a path into the server's raw answer. An endpoint without `rows_field`, or with a
  `response_field`, binds what it did before. `POST /api/http/...` returns the same rows as `rows`.
- **The Studio — an application built without leaving the page.** A new first page of the Liberty menu,
  **Studio**. Pick an application: its **map** shows, side by side, its menu, its screens, the data each
  screen uses (queries and endpoints) and the services its screens call. Select anything and what it is linked
  to lights up, the links drawn across the columns — a menu entry and the screen it opens, a screen and what
  it reads, saves through and calls, the screens inside its form, an endpoint and the service that serves it —
  while the rest fades. The panel on the right says what the item is and what uses it, and **edits it in
  place**: the screen designer, the query editor and the service designer open over the map and come back to
  it (double-click a screen or a service does the same). What nothing uses is shown as such: a screen no menu
  entry and no form opens, the application's queries and endpoints no screen uses, a reference to something
  that no longer exists. A search narrows every column; the integrity check's result is one click away.
  `GET /admin/studio/map`, `GET /admin/studio/overview`.
  A selection follows the links all the way: a query lights the screens that use it and their menu entries, a
  menu entry everything its screen leads to. An **endpoint** is edited in place too (the connector page's
  editor, in a window), as are the **menu** and the **dashboards** (their pages, opened over the map).
- **Jobs and endpoints are edited like services: the flow is the workspace.** The **job editor** is now the
  job's flow — start, a card per step with its kind, its name and what it does, the end — with what is picked
  edited beside it: a step's own fields (move, duplicate, delete there), or the job itself at the start (its
  schedule, presets, retries, alerts, parameters). A **+** on a link inserts a step at that place (a step could
  only be added last). The **endpoint editor** shows an endpoint as four stages — what it is → its inputs →
  the call (the HTTP request, or the function or service that serves it) → the answer (where its rows are, its
  columns) — and the picked stage's fields beside it. A **parameter** is a line (its name, its label, its
  default, "required") opened to edit its labelled fields, instead of two rows of unlabelled boxes.
- **One look for every editor's fields.** The fields of an API connector, an endpoint, a job and a job's step
  now carry their label above the control, as a service's step and the screen designer do.
- **Tests: an application's non-regression tests, in the framework.** A new page, Build → **Tests**. A suite
  names an API connector and holds cases; a **case is drawn as a flow of calls** — each card the endpoint
  called and what is expected, coloured by its last result — and what is picked is edited beside it: the case,
  or a step — its endpoint (the connector's list), its parameters as the endpoint's own form, what it expects
  (the call works or must be refused, a number of rows, the first row's values by column name; paths into the
  raw answer under Advanced). **Fill from the answer** plays the call and proposes what to expect. **Run this
  case** / **Run the suite** check the cases and compare them with the reference run; **Record the reference**
  keeps what the steps `keep`; a case that changes data is skipped unless the run allows it. Suites are TOML
  files (`<config>/tests/<suite>.toml`, packaged and versioned with the configuration), the last runs of each
  case and the reference are kept beside them. `liberty.apptests` (runner, store), `/admin/tests` (list, read,
  save, delete, run, try). It began as the JD Edwards tests of the nomajde application; nothing in it is JD
  Edwards', and any application's endpoints can be tested.
- **Test results.** Operate → **Test results**: how many cases passed, failed, changed or were never run, and
  per suite its last runs as bars (the latest on the right) with who ran them and the time taken, then each
  case with its last results as a strip, when it last ran, how long it took and what was found; a case opens
  in the Tests page. The Overview shows the same totals and links there. `GET /admin/test-results`.
- A job's run form can list the test suites: `choices_from = "test_suites"` (or `"test_suites:<connector>"`).
- **The tests have their own permissions**, so a functional team uses them without being administrators:
  `tests:view` (the suites and the results), `tests:run` (play them) and `tests:edit` (write them); each
  includes the one before, a superuser holds them all. They are in the role editor (Other access → Tests), and
  an application's menu may carry the two pages — `type = "page"`, `target = "/liberty/test-results"` or
  `"/liberty/tests"` — shown to who holds `tests:view`.
- **The Liberty menu, in the order things are built.** Build had fifteen entries; it is now **Build** (Studio,
  assistant), **Data** (connectors, dictionary), **Screens** (screens, menus), **Logic** (services, tests,
  notifications, AI) and **Insight** (dashboards, charts, Explorer, report templates). Integrity opens the
  Deploy folder: check, then deliver.
- **Long flows wrap, and zoom.** A service's, a job's and an endpoint's flow continue on the next row instead
  of running off to the right, and each canvas has a zoom (− / % / +, kept per kind of flow on the device).
- **The Explorer's table is the shared grid, and the result can take the page.** The table view and the
  detail rows are drawn by the grid of the data screens: search, a filter per column, sort on several
  columns, grouping, the column chooser and its own CSV / Excel export of what is shown; a column's code stays
  beside its heading; a row still drills, a detail row still opens its record. The detail stays paged by the
  server — the grid's pager shows the server's page and total (`serverPaging` on the shared grid). A
  **Definition** button beside the analysis picker hides the fields and the shelves: the result takes the
  page, with the view, the period, the Top and Run on one line. A saved analysis opens that way, a new one
  on its definition; the button stays at the same place in both states. A table inside a dashboard tile is
  the same grid without its toolbar.
- **An application can ship its own page.** A configured screen is a grid and a form; what is neither — a
  structure to browse, a guided flow — an application now ships as a page: one built JavaScript module per page,
  `plugins/<app>/pages/<page>.js`. A menu entry of type `page` with the target `/app/<app>/<page>` opens it in a
  tab; the framework serves the module (`GET /api/pages/<app>/<page>`, listed by `GET /api/pages`) and hands it
  what its own pages are made of — React, the shared components, the API client, the theme, a `call` that runs a
  function of the application — so the page imports nothing and looks like the rest. The permission is
  `page:<app>:<page>`, checked before the file is looked up and asked of the menu entry too; the role editor
  knows it. The contract is `frontend/src/pages/AppPage/sdk.ts` (version 1: fields are added, never renamed).
- **An Explorer entity can be an API endpoint.** An entity was a query or a table of an SQL connector; it can
  now name an endpoint with rows of an API connector (`connector = "<api connector>"`, `query = "<endpoint>"` —
  the endpoints a screen reads). Its fields are discovered from the endpoint's columns, labelled by the
  dictionary like a screen's; the engine reads the rows — under the caller's data scope — and filters, groups,
  measures, sorts and pages them itself, so the page, the saved analyses, the dashboards, the exports and the
  assistant's tools work on it unchanged. An `equals` filter on a field that is a parameter of the endpoint is
  sent to the endpoint too (a server that lists nothing without it, or narrows what is read). What such an
  entity cannot do: be joined with another entity, and carry a calculated field — both are refused with the
  reason. Each request calls the endpoint: the cost is the read's, capped at 50,000 rows.
- **An analysis or a dashboard of the Explorer goes in a menu.** On a saved, shared analysis or dashboard, **Add
  to a menu** (a superuser's: the menus are configuration) asks which application's menu, which folder and what
  label, and adds the entry. One click on it opens the Explorer on that analysis — its result at once, the
  definition one click away — or on that dashboard. The entry is shown to who may use the model
  (`explorer:<app>:<model>`), which a link to a whole model (`/explorer/<app>/<model>`) now asks too; a private
  object is refused until it is shared; an entry whose analysis was deleted says so. Route:
  `POST /admin/menus/publish`; targets `/explorer/<app>/<model>/a/<id>` and `…/d/<id>`.
- **A generated endpoint is modified where it was created.** "Create from…" made an endpoint easy to add; adding
  a column or a parameter afterwards meant editing its body, its row map and its parameters by hand. **Modify…**
  on a generated endpoint reopens the dialog on what it was built from, its choices as they were — tick a column,
  a field, a filter — and what the generator builds is merged into what you have: your name, label and parameter
  texts are kept, the differences are listed before anything is applied, and an application's read / add / change
  / delete endpoints follow together. For any endpoint, the columns of its rows are now a list (a column, the key
  of a row it is read from) instead of a line of text, and **Columns from an answer** calls the endpoint once and
  offers the keys of its first row to click.
- **A link can open a page on something.** `/job/<id>?<parameter>=<value>` opens a job's page with those values
  filled in; `/liberty/connectors?connector=<name>` opens the Connectors page on that connector, and with
  `&create=<generator>&q=<text>` its "Create from…" dialog on that generator with the text already searched.
  For a page an application ships, a notification, a document. The page contract gains `useCan` (does the caller
  hold a permission — to show a button only to who may use it).
- **A DELETE endpoint sends the body it declares.** An API connector dropped the body of a DELETE request, as
  for a GET. Some servers name what to delete in a JSON body — JD Edwards' Server Manager clears a cache that
  way — and answered "required field missing". A `DELETE` endpoint with a `body` now sends it (with its content
  type); one without stays without, and a GET / HEAD never carries one.
- **Integrity and Find usages read the assistant's playbooks.** A playbook (`ai.toml`) is prose the assistant
  follows — "query `list_runs` on the `nomaflow` connector" — so a query or an endpoint it names is one the
  assistant will call; nothing else in the configuration points at it, and Integrity reported such a query as
  *Unused*, Find usages as "0 references — safe to delete". Both now count a name written in backticks in a
  playbook when it is a query or an endpoint of the connector written with it (`` `nomaflow.list_runs` ``), of
  the playbook's application, or of a connector the text names; the connector is in use too. The other way
  round is a new error, **Assistant playbook**: a step that says "query `x`" when no connector the playbook can
  mean has a query or an endpoint of that name — the step would fail.
- **A page an application ships can use the shared grid.** The page contract (version 2) gains `Grid`: the
  grid of the data screens — search, filters per column, sort, grouping, the choice of columns, the export, the
  user's saved views — for a list of rows the page hands it. The page describes its columns in a small shape of
  the contract's own (`{ key, label, type?, mono?, muted?, value?, render? }`: a cell can draw a link or a tag
  while the column still sorts, filters and exports on its value) and gives the grid a box that has a height.
  Loaded with the first page that draws one: the entry bundle does not grow.
- **A query and an API connector are drawn as stages.** A custom query, a sequence or a lookup opens as three
  cards — **What it is** (label, description), **Statement** (its first word, the databases it is written for,
  in orange when it is empty or writes without being marked writable) and **Parameters** (how many are
  declared, the `:placeholders` of the statement) — each showing its part of the form; they replace the form's
  own tabs. An API connector's six tabs are the same cards: the server's host, the kind of authentication and
  what it still lacks, the environments, the endpoints and how many write, webhooks, the test. An endpoint's
  line has a button that opens its editor over the whole window — for one with many steps or a long body; it is
  the same editor on the same draft, written by the page's Save.
- **An analysis exported by the server follows label / code / both.** The Excel and PDF exports — the page's
  buttons, a dashboard of analyses, the report step of a job — wrote the raw codes. They now write what the
  analysis shows: the label by default, the code, or both (two columns in a table, `code · label` in a pivot or
  a chart). A lookup is read under the caller's permission and scope; one that cannot be read leaves the codes.
  **A scheduled report of an analysis with a coded column therefore shows labels from this version on** — set
  the field to *Code* to keep the codes.
- **The Tests page shows the buttons the caller may use.** Save, the new / delete buttons need `tests:edit`;
  Run, Record and "Fill" need `tests:run` (or `tests:edit`). A user who may only read sees none of them, and a
  line says so. The routes already refused; the page no longer offers what they refuse.
- **A coded column of an analysis shows its label, its code, or both.** In the Explorer, the chip of a field
  read through an enum or a lookup takes the choice: **Label** (as before), **Code + label**, **Code**. In the
  table, *Code + label* is two columns like on a data screen — `Company (ID)` then `Company` — each sorted,
  filtered and exported by itself; the pivot writes `101 · Alpine Foods SA` in its one label cell, and a chart
  keeps the labels on its axis unless the field is set to its code. The choice is saved with the analysis and
  followed by the dashboard tiles. Two things follow from one reading for every view: the pivot, which showed
  the raw codes, now shows the labels by default, and a code that is a number (a company number) is read
  through its rule in the table too instead of being printed as a figure.
- **A dashboard of analyses is laid out like the dashboards.** In the Explorer's **Edit layout**, a tile moves
  by drag and drop — its grip, a bar showing where it lands — and is sized by its corner: whole columns (1–4)
  and the closest of the three heights. The grip takes the keyboard too (arrows move, Shift + arrows size).
  The four small buttons (left, right, width, height) are gone.
- **A grid's pager counts the whole result.** From the second page on, the line under a grid read "Showing
  51–50 of 50": its total was the rows of the page in view. It now counts every row the pages share out, after
  the filters and the grouping ("Showing 51–100 of 5000").
- **An export cut by the analysis's own Top says so.** A table limited to its first 20 groups ended, in the
  workbook and the PDF, with "truncated at 3000 rows" — the export's own cap, which had not been reached. It
  now says "first 20 of 44 groups"; the 3000-row line is kept for the export's cap. The workbook's line
  follows the reader's language like the PDF's.
- **The Explorer page has the look of the editors.** What an analysis is made of reads as five cards, each
  with its coloured icon and title — **View**, **Period**, **Rows**, **Measures**, **Filters** (and
  **Options** for a pivot or a chart) — with its chips in its colour; the result is a panel whose head says the
  view and what the run answered (groups, rows, truncation); Save, Save as, Delete, Excel and PDF are in the
  page header beside the model picker. The dashboards of analyses take the same period card. Nothing behaves
  differently: fields are still clicked or dragged onto a shelf, chips still reorder by drag. The heading of the
  result table and of the pivot hides the rows that scroll under it (its background was see-through).
- **An Explorer model is drawn.** The Explorer builder shows the model as a diagram: the hub, the entities it
  reaches placed by how far they are from it, each relation a line carrying the columns it joins on — and,
  set apart in orange, the entities that do **not** reach the hub (an analysis cannot use them) and the
  relations that have no column pair yet. A click on an entity shows what it is (its query or table, key,
  measures), its relations, and Edit, Make it the hub, Delete; a click on a line edits that relation's column
  pairs; with nothing picked the panel holds the model's own settings (label, hub, description, preset). The
  layout is computed from the model — nothing is placed by hand — and the canvas zooms.
- **A screen that reads or saves through an API travels whole.** In a package and in a screen's history, the
  endpoints a screen reads (its rows) or saves through (its own connector's, or its "Saves through"
  connector's) were looked up as SQL queries on the API connector, not found, and listed as **missing
  references**. They are now what they are — API endpoints of the screen: listed under "API endpoints", ticked
  by default like its queries, and carried in the package; an endpoint served by a shared action brings that
  action with it. A screen saved from now on records them in its history (an older history entry keeps what
  it recorded).
- **Menus: drag an entry where it goes.** In the menu editor a row is dragged onto another: above it, below it,
  or into it when it is a folder — with everything under it; a line or a highlight shows where it will land.
  Each row has the icon of what it opens (screen, dashboard, job, query, endpoint, page), the tree and the
  picked entry are two panels that start on the same line, the entry's own actions (Clone, Find usages, Find
  dependencies) are on its panel, and Folder, Item, Discard and Save are in the page header.
- **Connectors: the SQL is where you look.** With a table or a query open, the connector's sections and the
  table's stages fold to one line each and the table's actions to a row of icons, so the statement starts
  much higher on the page. The **Build a SELECT / INSERT / UPDATE / DELETE** dialogs are wider and their
  preview takes the height the dialog has left, instead of a small box above an empty space.
- **Changes, Notifications, AI: the same frame.** Changes: the packages and the picked package are two panels
  (the list scrolls on its own), New package and Refresh are in the header. Notifications: Add rule is in the
  header, so the list starts on the same line as the rule; an empty page says how to start. AI: Discard and
  Save are in the header.
- **Release notes are read one version at a time.** The versions are a panel on the left, each with its number
  of entries; the picked version shows each entry as a card titled by the sentence it opens on, across the
  whole panel. A search finds an entry by its words **across every version** and says which version it belongs to.
- **Dashboards, Charts, Explorer, Report templates: the buttons where the others have them.** Add dashboard, Add
  chart, Add / Delete model and Save, New template are in the page header beside Reload config; the messages
  are banners under it.
- **History and Documentation reports: the same frame.** History: the files, screens and upgrades are a panel
  like every list of the editors, scrolling on its own. Documentation reports: the Settings card is as tall as
  the Content card beside it.
- **The Connectors page, SQL side: nothing hides the list any more.** A connector's line says what it holds
  (`sql · 117 tables · 34 queries`, `api · 68 endpoints`). Its sections are stage cards with their counts —
  Settings (its pool), Tables, Queries, Sequences, Lookups. **Opening a table keeps the list beside it**: the
  list narrows to a column (each table's four statements as dots) and the table opens next to it, so going
  from one table to the next is one click; the same for a query, a sequence, a lookup. A table is drawn as
  its stages — General, then **Read, Update, Insert, Delete** — each card saying whether the statement is
  set, and in orange when a write statement is there but not marked writable (it would be refused at run
  time). Connector, Discard and Save are in the page header. The fields are the ones they were.
- **The Dictionary page: a record says what it is in the list.** An entry's line carries its format and its
  rule (`LOOKUP` — its target in the chip's tooltip), an enum how many values it holds, a lookup or a sequence the other connector it reads from; the
  filter finds a record by its name, its label or one of those (type `lookup` to see every entry ruled by a
  lookup, or the lookup's name for those it rules). Each kind's tab says how many records the scope holds, the picked record's **Find usages** button
  says in how many places it is used, the list is a panel that starts on the same line as the form, and Scan a
  table, Export, Discard and Save are in the page header. The form of a record is unchanged.
- **Dashboards: cards are moved by drag and drop, and sized by their corner.** In the dashboard editor a card's
  grip is dragged onto another card — a bar shows on which side it will land — and its bottom-right corner
  sizes it, the card following the pointer in whole columns and rows with its size written beside the grip
  (`6 × 2`). The eight small buttons (W− W+ H− H+ ← →) are gone; the grip takes the keyboard instead: arrows
  move the card, Shift + arrows size it.
- **The Pools page: an address typed as an address, and a connection you can test.** A pool's line in the
  list says what it connects to (`postgresql · host:5432 / database`). The picked pool is drawn as its stages
  — **Database**, **Account**, **Schemas**, **Database links**, **Tuning** — each card saying what it holds.
  The database's address is typed as what its type needs, on one row: the **database type**, host, port and
  the one name that tells which database — a database, an Oracle **service name** (a SID when the URL is
  written with one), a SQLite file. Picking another type moves the pool to it: its driver, its usual port,
  and the name carried from a database to a service name or back. The driver (a dropdown), the options and
  the SQLAlchemy URL — composed from the fields, still editable — are folded under **Advanced**. A URL the
  page cannot read part by part
  (anything it could not put back identically) is shown alone and never rewritten; `${VAR}` references are
  kept as typed. A password written in the URL is flagged, with a button that moves it to the encrypted
  password field. **Test connection** opens one connection with the pool as it is on the page — saved or not
  — and answers with the time taken and the server's version, or the driver's own words
  (`POST /admin/config/pools/test`; nothing is saved, the running pools are not touched). Add, Discard and
  Save are in the page header.
- **The App page is a list of sections with the picked one beside it.** The ten to thirteen accordions in
  alphabetical order are now four groups in the order an installation is set up — **The installation** (server,
  display, external addresses, PDF branding, licence), **Signing in** (accounts, OpenID Connect), **What is
  switched on** (AI assistant, email notifications, nomaflow, change packages), **Running it** (server log, what
  the deployment sets) — each line saying its state (the model, "disabled", "no SMTP", "restart after a
  change"). One section is open at a time, at full height; the page remembers which. Every setting is still
  there, and **the fields line up**: one grid of columns for every section, each label above its field, in
  place of rows of inputs sized one by one. A label is short again: the explanation it carried in parentheses
  is a line under the field, in plain text.
- **Save and Discard are in the page header**, beside Reload config, on the App and Theme pages (and on each
  editor as it is redone) — no longer a row of their own in the page (`common/HeaderActions`: a page shown
  without a header keeps them in a row).
- **The Theme page shows what it changes.** The palettes are swatches to click, the fonts are cards written in
  their own font, and the preview is a small piece of the application — its menu, figures, a grid with a picked
  row, a field in focus, the buttons — drawn **in dark and in light** side by side, whatever mode you are in.
  The two native dropdowns are gone. **Advanced: CSS variables** edits the raw variables of `theme.toml`
  (`[theme.vars]`), which the page did not show — and **dropped on every save**: a variable set by hand is now
  kept, listed and editable.
- **Integrity and "Find usages" see what the jobs, the Explorer models and the notification rules name.** A
  query that only a job runs (a step's query, a file load's `refuse_query`, the choices of a run parameter), an
  Explorer entity reads or a notification rule's action calls was reported **unused** — and "Find usages" said
  "0 references", an invitation to delete a query a job needs. They are references now, each listed with its
  job and step, model and entity, or rule; a connector only a job names (a copy's source or target, a connector
  handed to a python step) is no longer "unused" either. The queries the dictionary's "Scan a table" reads by
  their name count as used by it. **A lookup that reads an API endpoint** (its rows, as a screen reads them) is
  no longer a "missing query" error; an endpoint that does not exist, or returns no rows, is. "Find usages" on
  an endpoint also lists the screens that read or save through it, the lookups and the notification rules.
- **The screen designer reads like the screen.** Its nine tabs are now **six stages drawn as a flow** — What
  it is → Data → Grid → Record form → Actions → Output — and each card says what the screen holds there: the
  query it reads and how many it saves through (in orange when none is set), the columns and filters, the
  record form's tabs and fields, the toolbar and row-menu actions, the export. A stage with several sections
  shows them under it (Grid: Columns, Views, Summary; Actions: Actions, Row menu). Every section is the one it
  was: no setting moved or left.
  **The columns are a list with the picked column's settings beside it**, as a service shows its flow and the
  picked step: each line gives the column's name, its label (the screen's own, else the dictionary's) and
  what is worth seeing without opening it — key, filter, hidden, required, read only, sensitive, its rule.
  Drag to reorder, filter the list, add, remove; the settings are the same form, its nested rules opened in
  the panel.
- **An overview page.** The Liberty menu now opens on **Overview**: the applications of the installation, each
  with its figures and what is loose in it (it opens in the Studio on that application), and **how an
  application is built** — eight steps in order (connect the data, name the fields, build the screens,
  organise the menu, add the logic, show the figures, give access, check and deliver), each with one sentence,
  what the installation already holds of it and the way in. "A new screen, with the assistant" starts from
  there.
- **Services, and their flow.** The Actions page is now **Services**: a shared action is a service. Its
  designer opens on a **graphical view** — the steps drawn as a flow read left to right: the start with the
  inputs, a card per step (query, endpoint, function, service, message…), a condition forking into its True
  and False lanes, a loop or a group framing its own steps, the answer at the end. A click on a card edits
  that step in the side panel (its target, its input mapping); a **+** on any link adds a step there; the
  former list is one click away (Flow / List). The layout follows the action's structure: nothing is placed
  by hand.
  On each service: **Run** — fill its inputs, see the answer it would give as an endpoint, the steps that ran and
  the time taken (the steps really run); **Expose** — create the API endpoint it serves, on an API connector of
  the installation or on the studio's own (`studio`, created on first use), with the service's inputs as the
  endpoint's parameters (an input the steps read as `INPUT.<name>` counts even when the action does not declare
  it). The endpoints a service already serves are listed on it. Routes: `GET /admin/studio/services`,
  `POST /admin/studio/run`, `POST /admin/studio/expose` (superuser).
- **Picking and filling in, made simpler.** A pass on what was hard to set by a person who does not write
  code:
  - **One picker for a function of an application**, the same everywhere a function is chosen (a job's
    function step, a service's plugin step, an endpoint served by a function, a tool of the assistant, the
    steps run after a change package): the list is **grouped by application and module**, each line the
    function's name and what it does in plain words, ordered by name — the application of the function already
    picked first. Under the field, what is stored (`module:function`) and the description in full; a name the
    list does not hold is said.
  - **A job's "load a file" step reads as five questions** — where the file comes from (given by the person who
    starts the job, a file on the server, a watched folder: one choice, which shows only its own fields), how to
    read it, the table it fills, what happens to the rows already there, what is checked. Labels are a few
    words, what a field is for is written under it. The columns named differently in the file and the values
    written on every row are rows with headed columns and examples; **the table's columns are offered as a
    list** once the table is named (also for the columns replaced and the column to total), and **the query that
    refuses the file is picked** among the connector's queries.
  - **Every dropdown is the shared one.** The last native dropdowns (the job step forms, a job's retry, the
    Run window's presets and log level, the report templates, a screen's row click, the steps after a change
    package, a report's format) are replaced: search, theme, the same look.
  - **A dropdown takes the keyboard when it opens**: the search box has the focus, typing filters at once (it
    had to be clicked first).
  - **A list of "code — description" lines is ordered on the code**, not on the description (the codes came out
    shuffled); a description is found by typing it.
  - The name = value rows of the job editors carry their placeholders in the user's language.
- **Fixes in the builders.**
  - **A service's Notification step can be set**: the rule is picked among the notification rules triggered
    "by an action" (a rule that is disabled or unknown is said), and the values its message needs besides the
    current record are given below. The step could be added but neither field was shown.
  - **Explorer → an entity keeps the fixed values of its query's parameters and "keep on upgrade"**: both are
    now fields of the entity window, which dropped them on save.
  - **Explorer → a model with no entity is no longer left out of a save in silence**: one that names a preset
    is kept (and the page draws it, offering to fill it — it failed to render); one that names none is refused,
    by name.
  - **Dashboards and charts carry "keep on upgrade"** as a tick box (it could only be set in the file), and a
    dashboard filter left unfinished is refused instead of being dropped.
- **Roles follow the menus: read, or read and change.** In the role editor a menu folder or entry is set to
  *Follows its folder*, **Read**, **Read and change** or *Hidden*, and that is all the role stores
  (`menu:<app>:<id>`, and `edit:<app>:<id>` for change). **The server works out what the screens under it
  need**: their read query, the lists behind their dropdowns, the tabs of their record form, their dashboards'
  queries — and, for *Read and change*, what writes: the screen's update / insert / delete, the queries and
  endpoints marked as changing data that its actions run, and the functions they call. Before, allowing an
  entry gave only its read query; the writes, the lists and the action targets had to be added one by one as
  permission strings.
  - **A role is a few lines** whatever the size of the application (a "master data, read and change" role on
    the demo JD Edwards menus: 7 lines), and **it follows its folder**: a screen an upgrade adds to a folder
    the role allows is covered without touching the role.
  - Each entry says what a tick gives ("4 to read · 22 to change", the names on hover).
  - The nearest choice decides: an entry set to *Read* inside a folder in *Read and change* stays read-only,
    a *Hidden* entry is taken out of an allowed folder. A deny of a permission by name still wins over
    everything.
  - Following a row click to another screen gives that screen's read only: changing it needs its own entry.
  - **Existing roles keep working** and read as the same choices; saved again from the editor they are
    written in the short form. **What changes for them**: a role that allows a menu folder or entry now also
    receives the lists and tabs of those screens (they were refused unless added by hand) and the screens
    later added to an allowed folder.
  - Not covered: a page shipped by an application gives the page, not the functions it calls (they are not
    declared anywhere) — add `plugin:<module>:*` under *Beyond the menus*.
  - **A role can be duplicated**: *Duplicate* opens a new role with the same rights and the same data scope.
  - `GET /auth/me` tells the browser what the menu grants give (`implied`), so a button a page shows or hides
    agrees with the server.
- **Predefined roles, shipped by the applications.** An application ships its roles in `roles.toml` (a label and
  a description per language, the menu folders in read or in read and change, the few rules beyond the menus).
  **Access → Roles → *Add a predefined role*** lists them by application; picking one opens it as a new role,
  ready to save — it is then the customer's, to change like any other (one already added opens as it is). An
  application install adds the new ones and refreshes those nobody edited in the file.
  - **A person may hold several roles: their rights add up** — a security officer who is also the licence
    manager. A predefined role therefore only gives: one holding a rule that takes away is refused when the file
    is read.
  - **The whole application has its own choice in the role editor** (above its menu: nothing, *Read*, *Read
    and change*, *Hidden*), which every folder follows unless it says otherwise — "the application in read,
    this folder in change" is two ticks, and the server reads a rule on a whole application as the outermost
    one.
  - **Running a job is part of *Read and change***: reading never starts anything. A job entry is shown to
    who may run it (it used to be shown to a role that named the entry without the job).
- **A page an application ships can give the user a file** (page contract version 6: `sdk.download(path, name, body)`): a report as
  a PDF, an export — the file a route of the server answers, saved under the name the server gives it. The applications'
  pages use it for their documents (the platform report, the OCI estimate, the weekly upgrade report).

- **A page an application ships can draw a diagram** (page contract version 5: `sdk.Diagram`): tiers as columns,
  hosts as boxes, a card per element with its state — a dashed outline for what is not declared —, the links on
  their paths with the evidence each one is drawn on, a legend, zoom. The picture is **laid out once on the
  server** (`liberty.diagram`: the model, the layout) and painted twice from the same coordinates: in the page,
  and as an SVG for a PDF report (`render_svg`: print colours, the state as a mark and a word, text cut to
  fit). The flows' canvas moved to the shared components (`common/flowKit`).
- **Reports: a picture on a landscape page has that page to itself.** The chapter that follows now starts on a
  new portrait page (it went on under the picture), and the heading written just above the picture goes onto
  the landscape page with its number — it used to stay alone at the foot of the page before, while the
  landscape page repeated it without its number. The table of contents still points at it.

## 7.0.83 — 2026-11-19

- **Display fixes after the documentation pass.** The Claude Desktop extension's settings (address, token, files
  folder, certificate) and its description are in French when the profile is in French at download. Monitoring →
  AI usage: the estimated cost follows the language (`38,20 $` in French) and the list is titled *Latest
  activity* (it also holds applied cards and MCP calls). A change package shows the assistant's writes as
  "Assistant IA" in French. The *Write tools* setting and the `ai:write` permission say they also cover a
  screen's actions and running a job. The Access tokens tab says what **End access** does for a client
  connected by sign-in. In these notes, every released version carries its date instead of "unreleased".

## 7.0.82 — 2026-11-19

- **AI assistant: several records on one card — `screen_write_many`.** "Create these five users", "set the
  status of these twelve lines": the assistant proposes one card listing every record (up to 50, of one screen)
  instead of one card each, and one Apply saves them in order, each exactly as `screen_write` does (rules,
  lookups, hooks, audit, change package, the record lock). Every record is checked before the first is written:
  if one is refused, nothing is written and the answer says which and why. Should a save still fail midway, it
  stops there and says how many were written. Same switch and permissions as `screen_write`.
- **MCP: the clients connected by sign-in are listed, and one can be ended.** A client that signed in through
  OAuth (Claude Desktop, Claude Code, claude.ai) held tokens nobody could take back, short of changing the
  password — which ended them all. Each consent is now a record (`ly2_oauth_grant`, on the auth pool: the
  client's name, when it connected, its last use, the day it ends — never a token): Profile → Access tokens
  lists yours with **End access**, refused from the client's next call (`GET /oauth/grants`,
  `DELETE /oauth/grants/{id}`; a superuser sees everyone's with `?everyone=1`). A client connected before this
  release keeps working until its access ends, and is not in the list; sign in again to see it.

- **MCP: several Liberty servers in one Claude Desktop.** Claude Desktop tells extensions apart by their name,
  and every Liberty extension had the same one: installing the extension of a second server (production and
  test, another customer) replaced the first. The extension is now named after its instance — the host of its
  address and the installation's environment (`[app] environment`, Administer → App): "Liberty Next – Test ·
  liberty-test.acme.com" — so they install side by side; the downloaded file carries the same name. The server
  also says which instance it is, in its title and at the top of its instructions to Claude ("use the tools of
  the instance the user names, and say which instance an answer or a change concerns") — read when the server
  starts. An extension installed before this release keeps working; download it again to get its own name.

- **AI assistant: run a job.** Two more write tools (`[ai] write_tools`, `ai:write`): `job_form` shows a job as
  its page shows it to the user — what it does, the parameters a user may set with their choices, its presets,
  whether it is running, its last runs with their state and error — and `job_run` is "Run now": the job's
  declared parameters and presets only, under `job:<id>`, through the functions of the job page's own routes. In
  the chat it is a card; the run is queued and goes on in the background, and `job_form` follows it. A job that
  loads a file the user brings asks for it **on the card**: one picker per file step, the kinds of file the
  job's upload takes; Apply sends the file through the job's own upload and runs the job with it — the file
  never goes through the model. From Claude Desktop the file comes from the
  extension's folder (below).
- **Claude Desktop extension: send a file for a job.** A new optional setting of the extension, **Files folder**:
  with it the extension offers two tools that run on the user's own computer — `local_files` (the spreadsheets
  and delimited files of that folder, and of no other) and `send_file` (one of them to Liberty, for a job) — and
  `job_run` then names the file. The file lands in the job's own upload area (`POST /mcp/files`, the personal
  token; the same kinds of file and size limit as the job page; `ai:write`, `job:<id>` and `[ai] mcp_changes`).
  Nothing runs on the upload. Download the extension again to get the setting. Not for the connector by sign-in,
  which has no access to the user's disk. Recorded like the other
  changes (the `apply` channel); over MCP it needs `[ai] mcp_changes`.
- **Monitoring → AI usage, reworked.**
  - *Cost*: an estimate at list price (USD) on the period, per user, per day and per model — a model without a
    listed price is left out and named. The prices are those of the release (`liberty/ai/catalog.py`).
  - *Channels apart from models*: a tool called over MCP and a card applied were listed as models named "mcp"
    and "apply". "By model" now holds what answered the chat's questions, and a new "By channel" panel counts
    the chat, the external clients and the applied cards.
  - *Changes made through the assistant*: a panel lists them — who, when, by which channel, what (the screen,
    the operation, the record's key, the job) and the reason of a refusal.
  - *Latest activity*: a run of MCP tool calls by one user is one line (it was one line per call), its calls
    shown when opened; a filter per channel and "failures only".
  - Six panels in two columns, none leaving half a row empty.
- **Sensitive data, corrections** (found while writing the documentation):
  - the messages of the actions a screen runs after a write (`on_insert`…) were filled with the sensitive values;
    they are masked like an action's;
  - a sensitive field is filtered only on a whole value the user gives (equals): a range or a "contains" let its
    values be found out count by count — in the Explorer and in `sql_query`; and any measure other than a count
    is refused (the average too, which the 7.0.81 note left out);
  - the **Sensitive** option of a screen column has three states in the editor (as the item says / yes / no): the
    checkbox wrote "no" as soon as it was unticked, with no way back — every optional yes / no option of the
    editors gains from it.
- The assistant's write tools follow `[ai] allowed_connectors`: a screen whose data is on another connector does
  not exist for them.
- The OAuth consent page says what the client will be able to do on THIS server — read, and change only if
  `[ai] mcp_changes` allows it — and how long the access lasts.
- The usage record no longer keeps the text of a note the assistant proposes.
- The default model of a new installation is `claude-opus-5-5` (`claude-opus-4-8` before); an installation that
  sets its model keeps it.
- AI assistant, `screen_write`: **a value picked in a list fills its sibling columns**, as in the form. Where a
  screen column's list sets other fields from the picked row (`return_binds` — on the Security Workbench, the
  object gives its product code), the screen now fills them when the assistant writes the value; a sibling the
  caller gives stays the caller's. `screen_form` names them (`also_fills`), the card shows them.
- **MCP: changes need their own switch — `[ai] mcp_changes`, off by default.** In the Liberty chat a change is a
  card the user applies; over MCP the only gate is the client's own approval, which its user can set to "always
  allow". A tool that saves something — a record through its screen, an action, **and a saved Explorer analysis
  or dashboard** — is now offered to an external client, and run for it, only when an administrator allows it
  (Administer → App → AI Assistant → "Allow changes over MCP"; read at each request). **On upgrade:** a Claude
  client that saved analyses over MCP no longer can until the switch is on; reading is unchanged.
- **What the assistant changed is on record.** An applied card (a record written, an action run) is kept in the
  usage record under the channel `apply`: who, the screen, the operation or action, the record's key, and the
  reason when it was refused — never the values. With the MCP rows of the same tools (which now carry the
  record's key too), Monitoring → AI usage lists every change made through the assistant. On a change-tracked
  screen, the change package names those entries "AI assistant", as it names an action's.

## 7.0.81 — 2026-11-19

- **AI assistant: sensitive data is not sent.** An installation decides what is sensitive and Liberty keeps it
  from the assistant — in the chat and through MCP alike.
  - *What is sensitive* is the installation's to say (nothing is flagged in what the applications ship): a new
    **Sensitive** flag on a dictionary item (Dictionary → an entry → Rule) covers every column carrying the item,
    on every screen, query and Explorer model — found as the data scope finds its columns (the screen column's
    item, the column's name, the name minus the JDE prefix). The same flag on a screen column covers a column
    without an item, or exempts one (`false`).
  - *The switch* is `[ai] hide_sensitive` (Administer → App → AI Assistant), off by default.
  - *What is then left out*: the flagged columns of the rows `sql_query` reads (the answer names the columns
    hidden, so the assistant can say so); a record's values on a write or an action card ("before", the record of
    a delete, the screen's confirmation and messages — the value the user asked to write is theirs and is
    shown); the page context (the clicked row, the column list, a filter typed on such a column); in the
    Explorer, a flagged field cannot be a row heading nor be read by a measure other than a count — it can be
    counted, and filtered on a value the user gives. The screens themselves are unchanged.
  - *What it does not cover* — to know before relying on it: what a user types in a question or attaches; a
    free-text column that is not flagged; what an API endpoint (`api_call`), an application's own tool or the
    operations tools return, since Liberty does not know their columns.

- AI assistant, writing through a screen:
  - **A record open in its form is not changed.** The form locks the record it shows; `screen_write` now refuses
    an update or a delete of a record somebody has open — the other user by name, or the user's own open form,
    which would otherwise write its old values back at its next Save. Said on the card before anything is
    proposed.
  - **A value far down a long list is no longer refused.** A lookup longer than its pool's row cap comes back
    cut, as in the form; when the checked value is not in the part read, the list is read again narrowed to that
    value before deciding.
- `[ai] max_iterations` — the tool rounds one question may use — defaults to **15** (8 before): a procedure that
  writes several records through its screens takes more rounds than a question (creating a JDE user: about
  eleven tool calls). An installation whose `app.toml` sets the value keeps its own.
- A screen's column filter and a role's data scope on a result column named like a reserved word (`ULUSER "USER"`
  on JD Edwards) failed like the assistant's filter did in 7.0.80 (ORA-01747, and ORA-01745 for the bind): the
  column is now referred to quoted in the three wraps, with a bind name of its own in the screen filter.
- Monitoring → Retention: the same squeezing as AI usage had on a short window — the list now keeps its height
  and the page scrolls.

## 7.0.80 — 2026-11-19

- **AI assistant: write through a screen.** Three new tools, off by default: `screen_form` (what a screen lets its
  user add, change, delete and run: the key, each column with its label, whether it is required or locked, its
  default and the values it accepts, and the screen's actions), `screen_write` (add, change or delete ONE record)
  and `screen_action` (run ONE action of the screen — a row menu's "Validate" / "Close", a toolbar's or a form
  tab's button). Nothing is done by another path than the screen's own:
  - a record is read first through the screen's read query, the form's rules are applied — a missing required
    column, a value outside its list, a column or a record the screen holds read-only (`editable_when`), a column
    that does not apply to the record are refused with the reason — and each statement runs through the function
    behind the screen's save: dictionary rules, sequences, the related 1:1 tables, the audit table, the data
    scope, the change package, the notification rules;
  - the screen's save hooks (`on_insert` / `on_update` / `on_delete`, the form's `on_save`) and its actions run
    on the server as the browser runs them — `run_query`, `call_api`, `call_plugin`, shared actions
    (`call_action`), `chain` / `if` / `loop` / `return`, binds from the record, a previous step or a built-in
    (`#LOGIN_USER#`…) — each step under its own permission and with the change-package capture of the screen.
    A `confirm` is the user's (below) and its message is shown; a `notify` message is returned; a value an
    action prompts for is given up front (missing and required: nothing runs); what only a browser does
    (`navigate`, `open_url`, `refresh`) is noted, and an action made of nothing else is not offered.
  Three locks, all needed: `[ai] write_tools` (Administer → App → AI Assistant), the new permission `ai:write`
  (Access → a role → Beyond the menus → AI) and the permission of each query, endpoint or plugin that runs, as in the
  screen. **Who confirms:** in the Liberty chat the tools only propose — the user gets a card (the record before
  and after, or the action with the screen's confirmation and what it runs) and nothing happens until Apply;
  over MCP the client (Claude) asks its user before calling a tool that is not read-only. As in the screen, a
  save, its related tables and its hooks are separate statements: a hook or a step that fails leaves what ran
  before, and the answer says so. A value taken from a query (a lookup) is checked against its list the way the grid's
  import checks it — the rule and the parameters that apply to that record, a list that cannot be read refused.
  Not yet: a form that runs actions when it opens (`on_load`) is refused with that reason.

- **MCP: a Claude Desktop extension, for a server that is not on the internet.** A custom connector is called
  from Anthropic's servers and so needs a public address. The profile's Access tokens tab now offers
  the **Claude Desktop extension** button (`liberty.mcpb`): opened with Claude Desktop, it runs a small bridge on the user's
  own computer that relays Claude's messages to `/mcp` with the user's personal access token — the server only
  has to be reachable from that computer (an internal address, a VPN). The file is built at download with the
  server's address filled in and carries no secret: the user pastes a token created in the same tab; a switch
  accepts a self-signed or company certificate. Nothing to install (Claude Desktop brings Node). The same rights
  as any MCP access: `mcp:use`, the user's permissions and data scope. `GET /auth/mcp/extension`.

- A filter given by column or dictionary item (the assistant's `sql_query` `filters`, a dashboard's filter) failed
  on a result column named like a reserved word — `ULUSER "USER"` on JD Edwards: ORA-01747. The column is now
  referred to quoted, by the name the database gives it.
- `screen_write` on a screen that is a tab of another record's form (Role Relationships under a user): the column
  linking it to the parent — hidden and locked in the form, which seeds it — must now be given on an insert and is
  accepted; before, giving it was refused and leaving it out wrote a record belonging to nobody. A record whose
  key holds a blank part can be named (the blank part given as an empty string), so it can be changed or deleted.
- AI assistant: a question that used all its tool rounds (`[ai] max_iterations`, 8 by default) ended on "no
  response" and an error — nothing for what the rounds had cost. It now ends on one closing answer, asked with the
  tools switched off: what was done, what was found, what is left, and that the user can ask to continue.
  Monitoring → AI usage shows such a turn as "Round limit". A procedure that writes several records needs more
  rounds than a question: raise `max_iterations` (Administer → App → AI Assistant) where the write tools are on.
 on a window too short for the whole page, the panels were squeezed instead of the page
  scrolling — the tools list cut, the latest questions out of sight unless the browser was zoomed out.

## 7.0.79 — 2026-11-19

- **MCP endpoint: sign in instead of pasting a token (OAuth).** A client given only the address of `/mcp` — a
  custom connector in Claude Desktop or claude.ai — now finds the sign-in by itself: the browser opens Liberty,
  the user signs in the usual way (password or SSO) and allows the client on a consent page; nothing to create,
  nothing to paste. Liberty is its own authorization server (OAuth 2.1 as the MCP specification lays it out:
  discovery under `/.well-known/`, client registration, authorization code with PKCE, rotating refresh token),
  and nothing is stored: the client's registration, the code and the refresh token are signed tokens. The access
  is the same as with a pasted token — the user's permissions and data scope, re-read at every call, permission
  `mcp:use` — and ends the same way: a password change, a deactivated account or the loss of `mcp:use`. The
  consent lasts `[ai] mcp_token_max_days`; after that the user allows the client again. A personal access token
  pasted as a request header keeps working (Claude Code, scripts). Behind a proxy, set `[app] public_url`
  (Administer → App) to the address users reach the server at: the addresses the sign-in publishes are built on it.

## 7.0.78 — 2026-11-18

- **Licence usage report.** A licensed installation tells the licence management tool, once a day, which
  environment it is, its version — the framework's (`liberty-next`) and, when the applications are installed, theirs
  (`liberty-apps`), one report each — and its number of enabled accounts — nothing else: no figure of the applications,
  no name, no data. It is sent by a nomaflow job the framework ships itself, `liberty-usage-report`, on purpose in
  the open: it is listed under Nomaflow → Jobs, each run shows what was sent and what the tool answered, a refused
  or unreachable send is the run's error, and it can be run now, paused or rescheduled like any job (its first
  change writes it into `jobs.toml`). The job exists only where there is a licence key; `[license] usage_url`
  (Administer → App → License) is where it reports — blank it and nothing is sent.
- `[app] environment` (Administer → App): what the installation is — "production", "test"… — named in that report.
- nomaflow: the framework can ship jobs of its own (`liberty.jobs.builtin`), added to the list unless `jobs.toml` has
  a job of the same id.
- AI assistant: **attach a file to a question**. A paperclip in the chat lets the user give the assistant a file to
  read: an image (a screenshot of an error), a PDF (a procedure), a spreadsheet or a CSV (a list to compare with what
  the installation holds), a text or log file. An image and a PDF go to the model as they are; a spreadsheet, a CSV
  and a text file are sent as text — the first sheet, the delimiter detected, cut beyond a size with a mark on the
  file. The assistant only reads it: nothing is loaded into a table, nothing is stored. The file stays with the
  conversation while it is open; the saved conversation keeps its name only, and a resumed question says its file
  is no longer there. `[ai] attachment_max_mb` (10; Administer → App → AI Assistant; 0 = no attachment). A file is
  input tokens at every question of its conversation.
- A server run from a checkout of the applications' repository (development) now knows the applications' version —
  from the repository's `pyproject.toml` — where it knew it only for an installed wheel or a materialised bundle:
  the usage report, the upgrade journal and the release-notes page name it.
- **MCP endpoint: `/mcp` was unreachable on a real server.** The endpoint answered `/mcp/` only; the bare `/mcp` — the
  address a client is given — fell to the application's page, which refuses a POST (405): Claude's connector check
  reported "Connecting to the server — 405". It went unseen because the tests ran without the page and followed the
  redirect that then exists. The endpoint is now routed at `/mcp` itself, ahead of the page. On 7.0.77, give the
  connector `https://<server>/mcp/` with the trailing slash. The OAuth discovery addresses
  (`/.well-known/oauth-protected-resource`, `oauth-authorization-server`) now answer a plain 404 instead of the page.

## 7.0.77 — 2026-11-18

- **MCP endpoint** (`/mcp`) — the assistant's tools from an external client: Claude Desktop, claude.ai or Claude Code
  reach a Liberty installation through a custom connector and act as a Liberty user. Nothing of the tools is written
  again: the client is offered what the user is offered in the chat, a call goes through the same permission checks
  and the same data scope, and every call is in the usage record (Monitoring → AI usage) under the `mcp` channel.
  The tools whose result is a card of the Liberty chat (the scaffolding proposals) are not offered; a tool that saves
  something says so to the client. Off by default: `[ai] mcp_enabled` (Settings → App → AI, applied without a
  restart). Needs the new `mcp` extra — the official `mcp` package — which `liberty-next[all]` and the image include.
- **Personal access tokens** — what a user gives the connector in place of a sign-in. A user holding the new
  permission `mcp:use` creates one in their profile (the new **Access tokens** tab, or `POST /auth/tokens`): it is
  shown once and not stored, valid 90 days by default and `[ai] mcp_token_max_days` (366) at most. The account is
  re-read at every call, so the token carries the user's rights of the moment, never more: it ends at its expiry,
  when the account is deactivated or loses `mcp:use`, and when the account's password changes — which is how a
  leaked token is ended (there is no list of tokens to revoke one by one). A personal token opens nothing but the
  MCP endpoint; the rest of the API refuses it. To connect Claude: a custom connector on `https://<server>/mcp` with
  the request header `Authorization: Bearer <token>`.
- AI assistant: `sql_query` runs a query the way its screen does. Since its first version the tool called the
  connector directly — a "bare SQL runner", as the route's own documentation put it — so everything a screen brings
  was missing: the column labels and formats, the screen's filters, the dictionary filters, the environment of the
  page. It now goes through the same function as the screens' route (`web/connectors.select_events`, extracted from
  the streaming route, which uses it too). The tool takes `filters` — `{column: value}`, sent as the framework's
  dictionary binds (what a dashboard filter sends): the connector finds the column, types and trims the value, with or
  without a screen; a range is `<column>_from` / `<column>_to`. A filter that matches no column is refused instead
  of being ignored. On a JD Edwards install, "the UDC types of system 01" or "the data item AN8" is one call
  instead of reading the table 200 rows at a time.
- `describe_instance` and `search_catalog` give a screen's connector when it is not the application's own (the
  assistant called `nomajde.f0004_get` for a query that is on `jdedwards`).
- AI assistant: the applications ship tools. `ai.toml` takes `[ai.<app>.tools.<id>]` — a function of the application's
  plugins (`callable = "module:function"`, one of the `j_` entry points), the description the model picks it by, and
  the permission the user needs (`plugin:<callable>` unless another is named). The model sees it as `<app>_<id>`, with
  the function's own parameters as arguments (types from the hints, descriptions from the docstring); `connectors`,
  `settings` and `ctx` are supplied by the server, never by the model. A tool is offered only to a user holding its
  permission, only when its application is loaded (a licensed connector without its key ships nothing) and allowed to
  the assistant; a function that is not a plugin entry point, or cannot be imported, is left out with a warning.
  Read-only by contract. Edited under **AI → Tools**, applied on a reload without a restart; `[ai] app_tools = false`
  (Settings → App → AI) switches them all off.
- Settings → AI: a save no longer drops what the page does not show — the starters per language (`l.fr`) were lost on
  every save, a screen's starter groups when its questions were edited.
- The grid's Summary view works on an Oracle query: its count column is named `_count`, which Oracle refuses
  unquoted (ORA-00911) — every summary on an Oracle pool failed. The name is now quoted in the generated SQL.
- AI assistant: the conversations are kept for the user. The chat drawer saves each conversation on the server (the
  per-user saved-views store, a new kind `chat`) instead of only in the browser: a **history** button lists them —
  resume, delete — and they follow the user from one device to another. What is kept is what the user saw (the
  questions and the answers); a resumed conversation reads its data again. 30 conversations per user at most, the
  oldest dropped.
- AI assistant: **notes**. Ask the assistant to remember something for later conversations — a convention, a
  preference, how a figure is defined — and it proposes a note on a card; nothing is saved until the user accepts
  it. The user's notes for the page's application (and those for every application) then ride with their questions.
  They are private to the user, listed and deleted in the same panel as the conversations (kind `ai_note` of the
  saved-views store).

## 7.0.76 — 2026-11-18

- Menus: an application is listed for what the user may open in it. **A change of behaviour for restricted users**:
  a dashboard entry was shown as soon as the dashboard existed and the Data Explorer page to everyone, so a user with
  the permissions of one application saw every application in the switcher — each with a dashboard that rendered
  empty and an Explorer with no model. Now a dashboard entry is shown to who can read at least one of its data
  widgets (or holds `dashboard:<id>` or the entry's `menu:<app>:<id>`; a dashboard of texts and links only stays
  shown), the Data Explorer page to who may use a model of that application (`explorer:<app>:<model>`), and links
  that need no permission (an open page, an external address) no longer list an application by themselves. Nothing
  changes for a superuser or a role holding `*`. To check a role: Settings → Access shows what it sees — the role
  editor follows the same rule (`GET /admin/access/menu-rules`). The AI assistant, which names the applications by
  this rule, follows.
- Image: the security fixes of the base system are applied at build (`apt-get upgrade`: apt, zlib) and `pyasn1` is
  0.6.5 — the release's image scan refused 7 HIGH findings, all with a fix available.
- AI assistant: an audit and usage record. Each question to the assistant is kept in `ly2_ai_call` (created at
  start-up, on the auth pool): who asked, from which application's page, the model, the tokens (input, output, read
  from / written to the cache), the time, how it ended, and each tool call — its name, what it was called on (the
  connector and query, the model, the keywords), whether it failed and why, its size and its time. **Never the
  question, the answer or a row a tool returned.** A question the user abandons half-way is recorded with what had
  happened; a store that is down never breaks a conversation.
- **Monitoring → AI usage** (superusers): the period in figures — questions, users, tokens with the share read from
  the cache, tool calls and failures, average time — by user, day, model and tool, and the latest questions with
  their tool calls (a click on a user narrows them). `GET /admin/ai-usage`.
- `[ai] usage_retention_days` (90; Settings → App → AI; 0 keeps everything): older questions are dropped with the
  notifications' purge, and the store is on **Monitoring → Retention** (size, preview, purge).
- AI assistant: the tools answer for the signed-in user. **A change of behaviour**: until now `ai:chat` alone let the
  assistant run every read query of the connectors it is allowed (the data scope applied, the permissions did not).
  Each tool now checks what the screens check: `sql_query` needs `sql:<connector>:<query>`, `api_call`
  `api:<connector>:<endpoint>`, the Data Explorer tools `explorer:<app>:<model>`; `list_connectors`,
  `describe_instance`, `search_catalog` and the playbooks show only what the user can open (a screen by its read
  query, a menu entry by the menu's own rule, a job by `job:<id>`) — an application with nothing for the user is
  not named at all, and a refusal says the same for a query that exists and one that does not. The operations and
  scaffolding tools are offered to superusers only. A user who had `ai:chat` without the `sql:` / `explorer:`
  permissions of an application gets fewer answers from the assistant: grant them, as for the screens.
- AI assistant: finding a query or a screen no longer means reading the whole catalogue. `list_connectors` lists the
  read queries only (a writable one was listed although the assistant can never run it) and, on an installation too
  large to list at once, returns an overview — the model then narrows with `connector` (one connector's list) or
  `search` (keywords on the name, label or description). `describe_instance` takes an `app`, and a new tool,
  `search_catalog`, finds a screen, a menu entry, a dashboard, an explorer model, a job or a playbook by keyword. On a
  large installation the full list was cut at the tool-result limit — the model could not see part of the queries —
  and was paid again on every round of the conversation.
- AI assistant: the conversation is cached as it grows. Within one question each tool round sent the earlier rounds —
  their tool results above all — again at the full input price; they are now read from the prompt cache (automatic
  caching on the request, next to the cache point on the system prompt). The evaluation runner shows the share of
  the input read from the cache.
- AI assistant: an evaluation set. `python -m liberty.ai.evals <case files>` plays questions against a live server
  (the account of `LIBERTY_E2E_USER` / `LIBERTY_E2E_PASSWORD`, so the permissions and the data scope are part of what
  is measured) and checks each answer: the tools called, texts and figures it must carry, no failed tool call. It
  reports the pass rate, the tool calls, the tokens and the time, and `--out` keeps the full results as a baseline.
  Run by hand before and after a change to the prompt, the model or a tool; the applications ship their cases.
- AI assistant: saving Settings → App no longer removes part of the assistant's tools. The save rebuilt the assistant
  without the instance tools — `describe_instance`, the playbooks, the Data Explorer and the operations tools — so
  until the next restart or reload it answered from the connector queries alone.
- AI assistant: `sql_query` has its own row cap — `[ai] sql_max_rows` (200 by default, Settings → App → AI). It
  inherited the pool's `max_rows`, which is sized for the grid (rows streamed): on a pool at 500 000 one question could
  read a whole table into a single tool result. Beyond the cap the result says `truncated` and tells the model not to
  count or add up the rows it got; the model can ask for fewer with `limit`.
- Pools: `[connectors] pools_path` in app.toml names a local file whose `[pools.*]` replace the same-named pools of
  connectors.toml (a pool it doesn't name still comes from there) — for the server, the CLIs (`liberty-ops` backup /
  doctor, `liberty-admin`, `liberty-connectors`) and hot reload; the Pools editor then saves to it. For a development
  machine whose connectors.toml is the apps repository's: its own hosts and passwords stay out of the shared file,
  while the connectors edited there are still committed. Unset (the default), nothing changes.
- The Pools and Connectors editors no longer flatten `${VAR}` references on a save: the editor reads the values
  resolved, so saving wrote back the resolved value — `${POSTGRES_HOST:-pg}` became the host it pointed at, and a
  later change of the environment had no effect on that pool. A value left as it was is now written as its
  reference again; one the user changed is written as typed. A password given as a `${VAR}` reference is no longer
  encrypted into an `ENC:` copy of the reference.
- `./start.sh` loads `config/app-dev.toml` when it exists and `LIBERTY_APP_CONFIG` isn't set — a developer's own
  settings (gitignored), so the committed `config/app.toml` stays as shipped.

## 7.0.70 — 2026-10-30

- nomaflow: the run detail has a **Back** button — to the page the run was opened from (the jobs list through a job's
  status, the runs list, a parent run); opened from a link, to the jobs list.

## 7.0.69 — 2026-10-30

- Accounts: a new password at the first sign-in. An account created — or reset by an administrator — with a temporary
  password ("Ask for a new password at the next sign-in" in Settings → Access, on by default for a new user;
  `liberty-admin create-user / set-password --must-change`) opens on a "Choose your password" page; until it's done the
  server refuses every call but the change itself (403 "password change required" — enforced on the server, not only
  on the page). Local accounts only: an SSO account is never asked.
- Sign-in: "Forgot password?". The user gives the username and the account's e-mail; when both match a local account a
  link is mailed — valid one hour, and once (it is bound to the password it was issued for). The answer is the same
  whether or not they match, so it never tells who has an account; requests are throttled per address. Offered when
  SMTP is set (`[notify]`); the link points at the new `[app] public_url` (Settings → App), else the browser's
  address. New public routes: `POST /auth/forgot-password`, `POST /auth/reset-password`.
- nomaflow: a step can be turned off for an install — `disabled_steps = ["SECURITY_LDAP"]` on the job, a checkbox
  in the step editor. It never runs, even when a run or a preset selects every step (the run dialog shows it off and
  not selectable); it is recorded as skipped, not failed. An operator setting like the schedule: the apps installer
  keeps it, and it doesn't make the job look changed (so the vendor's fixes to its other steps still arrive) — unlike
  a step's own `enabled`, the vendor's default a run may override.
- `./liberty apps` and `./liberty upgrade --apps` restart liberty-next after installing the wheel when the stack
  was already running: `docker compose up -d` only recreates a container whose configuration changed, so on an update
  the server kept the plugins and configuration it had loaded before, until a manual `./liberty restart`. A fresh
  install (the container created by that same `up -d`) is not restarted twice.

## 7.0.68 — 2026-10-29

- nomaflow: a step can let the job go on when it fails — `continue_on_failure = true` (on the step, or for every step
  with the job's `step_defaults`; a checkbox in the step editor). After its retries the step is recorded failed, the
  next steps run, and the run ends failed with "N step(s) failed, the others ran — <step>: <error>". For a job that
  chains independent work, where one part failing must not skip the others. Default unchanged: a failed step stops
  the job.
- API connectors: one server per environment. With several JDE environments on one SQL connector (`pools`), its AIS
  calls go to the AIS of the environment on screen — `pool` names the environment of the connector's own server,
  `targets` gives the other environments' servers (URL, user, password, token, TLS check; the endpoints stay shared).
  Nobody picks the server: it follows the environment chosen in the menu, a job step's `pool` (any step type now), and
  covers actions (`call_api`), plugins (`nomajde/ais.py`), `/api/http` and the assistant. A call for an environment
  of those connectors with no server is refused ("no target for pool …"), never sent to the default one (often
  production). Settings → Connectors → Environments edits them (passwords encrypted on save) and flags an environment
  without a server; so does Settings → Integrity. The apps installer keeps an install's `pool` / `targets` like its
  pools, and still brings the vendor's new endpoints in.
- Actions: a `confirm` step asks again. No screen passed the question dialog to the action runner (the form, the row
  menu, the toolbar, bulk edit), so a `confirm` only logged a warning and the chain went on without asking — nomademo's
  "Close this period?" / "Reopen this period?" among them.
- Actions: a `confirm` or `notify` message is filled like an `open_url` template — `{FIELD}`, `{INPUT.x}`, a chain
  path, the built-ins — and `#POOL#` names the environment the screen runs on: the pool picked in the menu when the
  screen's connector has it, else that connector's own pool — the one its AIS calls go to. "Reset the password of
  {ULUSER} on #POOL#?". (`/api/connectors` gives each SQL connector's default pool name along with its pool names.)

## 7.0.67 — 2026-10-29

- Record form: a date-time field (format `datetime` / `timestamp`) edits its day and its time — the date picker and a
  time field (`hh:mm` or `hh:mm:ss`) — instead of the day only, which saved it at midnight (`common/DateTimePicker`).
  Read-only, a date-time with a time zone shows in local time, like the grid (it showed the raw UTC value).
- Writes: a `SYSDATE` stamp takes the dictionary's date / time format when the screen shows the column with another
  format — a JDE time displayed as a plain `number` (F00950.FSUPMT, F95921.RLUPMT, F0004.DTUPMJ…) got the raw
  date-time, which Oracle refused for the NUMBER column (ORA-00932). Every "Update or insert" import of F00950 failed
  on it (its update probe does not send the time); since June.
- Import dialog: the rule check loads the lists of values each row needs — a lookup the grid had not loaded yet (an
  Import opened as the screen opens) or a list narrowed by the row's own values — instead of skipping it and passing
  the row as valid (`ZZ` went through as a language). A list that cannot be loaded makes the row an error.
- Record form: when a lifecycle hook fails after the row is written (an `on_insert` API call answering an error, with
  `stop_on_error`), the form stays open with the error — the refresh closed it and hid the message. `onSaved` only
  refreshes now; the form closes itself after a successful save or delete. A tab action asking for a refresh still
  closes the form (left open, it would keep the values it loaded and a later Save would write them back).
- Record form: a tab action whose prompt is cancelled reports nothing — it showed "<action> · OK".
- Bulk edit: "Clone row" and paste keep every value, keys included, except a column filled by a `SEQUENCE` (its
  `rules`): left empty, the insert takes a new one — and the 1:1 related rows follow the new key (the save looked the
  parent key up in the wrong case and the related insert failed on its foreign key). Columns carry `sequence`.
- SQL editor (and every read): a binary value (an Oracle BLOB) comes back as `(binary, N bytes)` — the JSON encoder
  failed on it and the request ended in a bare HTTP 500; the SQL editor answers any other driver error with its
  message.
- A link opened in a fresh browser (no application remembered yet) opens its page, its application selected — it
  landed on the first application's home.
- Formats `url` / `email` / `color` (dictionary entry or column): the grid shows a link opening in a new tab, a
  `mailto:`, a colour swatch beside the code; the record form's input gets an open button, or a swatch opening the
  system colour picker. Only safe values: a link is `http(s)` only (`www.…` gets `https://`), an e-mail must look
  like an address, a colour is a hex code or `rgb()` — anything else stays text. Sort, filter and export keep the raw
  value.
- Dates: a themed date picker (`common/DatePicker`) replaces the browser's own calendar — white, and different on
  each browser — in the record form, the grid's bulk edit, the grid's column filters and the Data Explorer's filters:
  a field where a date can still be typed (an impossible day goes back to the value) and the period picker's month
  calendar, with Today and Clear. The value stays an ISO date. The calendar is shared (`common/MonthGrid`); its title
  opens a month / year chooser (the 12 months, the year by arrows or 12 at a time) — in the period picker too — so a
  date a year away is three clicks, not twelve. A column filter on a date-time with a time zone compares on the local
  day, the day the grid shows. The Data Explorer's date-time fields filter with it too (by day).
- Data Explorer: a day in a filter on a date or date-time field is that whole day — `≤` / the end of a "between" keep
  that day's rows after 00:00, `=` / `≠` mean in / out of that day (they compared with midnight).
- Dates: a date-time carrying its time zone (a PostgreSQL `timestamptz`, `…+00:00`) shows in the browser's local time
  (`2026-10-29 00:54:08`), like the period filter's days — it showed the raw UTC value, a check made at 00:54 in
  Paris reading as the day before. Values without a zone (Oracle `DATE`, JD Edwards dates) show as they are.
- The period in the advanced filters fills its field like the other inputs, at their height (it left a gap and sat
  higher than its neighbours).
- Screens: a date column in the advanced filters is a period — the dashboards' date picker (presets and a two-month
  calendar), sent as `<COL>_from` / `<COL>_to` — instead of one exact date. A column's new `filter_period` (Filter
  group of the Visual Designer) sets the preset the screen opens on (`last7`…; blank: every row); the first automatic
  run already applies it. A drill or link passing a date on a date column opens that one day.
- Periods on a date column: a period ending on a day covers that whole day (`< the next day`) — a date-time column,
  or a date column holding a time (Oracle `DATE`, a timestamp declared as a date). It used to stop at 00:00 — a period ending today, or a single day, dropped that day's rows — on the screens and on the dashboards.
- Development: end-to-end regression tests (`frontend/e2e/`, Playwright, `npm run e2e`) drive the data screens on a
  live server and check what they save in the database — the record form (add on several tabs, duplicate, delete),
  bulk edit (add, change, clone, delete), the lifecycle actions and the row menu; test records only, never run in CI.
- Record lock: a record opened in another tab or by another user now opens read-only with the "locked by" banner even
  when this tab connected after the lock was taken. The server refused the second lock, but the browser only knew of
  the locks taken while it was connected, so the form stayed editable.
- Visual Designer: a save rewrites only the screens that changed. Every other screen keeps its table as it is in
  `screens.toml` (the keys the model ignores — a screen-level `l` —, an explicit `key_columns`), and the edited one is
  written without its default values, in the file's key order — editing one label changes one line. The save used to
  write every default of every screen (the file grew by 15 %: 30 724 → 35 300 lines on the apps' file) and dropped the
  keys the model ignores.
- Internal (no change of behaviour): `liberty/web/admin.py` (3 800 lines) is the package `liberty/web/admin/`, one
  module per section (versions, builders, assistant, module catalogues, theme, jobs, app settings, usages / packages,
  proposals, clone, delete, rename…) — the routes register in the same order, every name stays importable from
  `liberty.web.admin`; the SQL connector's pure text helpers (ORDER BY split, aggregate / filter wraps, schema / dblink
  placeholders) are `liberty/connectors/sqltext.py`. Frontend: the SQL Wizard's parser and generator are
  `services/sqlParse.ts`, the data grid's column helpers `resultColumns.ts` and its inline-edit cell `EditCell.tsx` (all
  with tests), its row context menu the hook `useRowMenu.tsx`, its batch edit (pending edits, new / deleted / selected rows,
  the clipboard, the Save and its row hooks) the hook `useBatchEdit.ts`, its import (file, Import dialog, paste:
  header mapping, rule validation, lookup return-fill) the hook `useImport.ts`; the record form's row lock is the
  hook `useRecordLock.ts`; the action prompt (`prompt_fields`), copied in both, is one hook `useActionPrompt.tsx`, and the builders share their page frame, toolbar and empty line (`common/builderShell.ts`).

## 7.0.66 — 2026-10-29

- Developer reference → Impact: every configuration entity (pools, connectors, queries, API endpoints, dictionary
  items, screens, menu items, charts, dashboards, shared actions, Explorer models, jobs, notification rules, AI
  blocks) with what it uses and what uses it, followed through — what a change reaches, counted by kind (a query →
  its screens → the menu items opening them, the charts → their dashboards, the jobs and actions running it); the
  used-by tree, the direct uses and everything the entity needs; a node re-centres the view (with Back), "Open in
  editor" goes to its builder; the entities nothing points at (entry points excepted) and the references to
  undefined entities are listed (`/admin/dev/impact`). The links are the dependency walk of Package & Deploy.
- Package & Deploy / promotion: a menu item now carries its target — the walk followed only the v1 item types
  (table / form / chart), so a `screen`, `query`, `endpoint` or `job` item reached nothing but its connector; a
  dashboard named `<app>.<id>` (as menus name it) resolves in its app instead of the first app holding that id
  (`overview` exists in several); a notification rule carries the screen or the jobs its trigger names.
- Developer reference → Command line: every command the package installs, read from its entry points and its
  argument parser — what it does, its usage (copy), the options of every command, each sub-command with its arguments
  (value, default, choices, required, repeatable); left, the commands with a search (`/admin/dev/cli`). The technical
  report's command-line section is built from it and now lists each command's arguments. `liberty-crypto` and
  `liberty-license` build their parser in `build_parser()` like the other commands.
- Developer reference → Impact: the groups on the left start folded, with Expand all / Collapse all; a search or the
  unused filter opens the groups holding matches.
- API documentation: the responses of the main read routes — connectors (list, describe, run a query, call an
  endpoint, schemas, import), menus, screens, the external addresses, the Data Explorer (models, a model's fields,
  aggregate and detail queries, saved analyses / dashboards) and nomaflow (jobs, run now, runs, a run's detail) — are
  documented (`/docs`, Developer
  reference → API: the Response section). Documentation only: declared beside the error responses, the routes return
  exactly what they did; a test validates each route's JSON against its model so the documentation cannot drift.
- Developer reference → Command line: the scripts on the left start folded, with Expand all / Collapse all.
- `liberty-crypto` no longer has a `decrypt` command: a secret is never printed back in clear from the command line
  (a terminal, a log, the shell history holding `--master-key`). Nothing used it — the server, the pools and the
  jobs (nomasx1's LDAP and audit-trail steps reading `settings_applications`) decrypt in their own process; `encrypt`
  and `is-encrypted` stay. To change a secret, encrypt the new value.
- Documentation reports: every report built is kept on the server (`<config dir>/.reports/`, next to `.versions/`, so a
  backup carries it) with who built it, the language and the sections; the report's page lists its kept copies to
  download again or delete (`/admin/dev/report-copies`), the Retention page shows their number and size. A copy
  that cannot be written never blocks the download.
- Developer reference → API: a route's request / response example is no longer squeezed to one line in its detail
  (the block shrank when the detail scrolled); the same for the error list of the grid's Excel import.

## 7.0.65 — 2026-10-29

- Developer reference (a new page in the sidebar, superuser; documentation, nothing is edited there):
  every setting of app.toml, read from the running code — sections on the left with what is not in
  the UI yet, the grid on the right (type, the screen that edits it or why it stays in the file /
  the environment, the modules that read it, default; filters, export), and a row opens the detail
  with each line of code that reads it (`/admin/dev/config`).
- Settings → App: every setting of app.toml is now set from the screen — new sections Server log
  (console format, the file on disk or off, its size and rotation), Sign-in (accounts store and
  pool, token issuer and lifetimes, sign-in throttle), nomaflow (on / off, pool, run log lines,
  work area, largest upload) and Change packages (on / off, pool, retention); the sub-path
  (`base_path`), the OIDC attributes read from claims and the session secret (stored encrypted), the
  Explorer tools of the assistant, the inbox pool. What stays in the file — the paths of the
  configuration files, the signing algorithm, the master key and the JWT secret — is listed
  read-only with the reason. A test now fails when a new setting is neither editable nor explained.
- Settings → App: editing only the notification settings now enables Save (they were left out of
  the unsaved-changes check).
- Developer reference → API: every route of the running server — method, path, summary, the module
  it belongs to (by category: Access, Workspace, Features, nomaflow & deployment, Settings,
  Operations, Assistant) and the access it requires, read from the code (public, any signed-in user,
  superuser, or the permission it checks such as `sql:{connector}:{query}`); a route opens on its
  handler (file:line), description, parameters, request (its fields and an example JSON) and
  response (from the declared models); links to /docs and /redoc (`/admin/dev/api`).
  Configuration now shows each setting's label as in its screen.
- Developer reference → Data model: the framework's own tables (accounts, nomaflow, change packages,
  saved views, notifications — from their models: columns, types, keys, references) and each
  connector's declared tables — the physical table its read query selects from, its key, its queries,
  the screens / dashboards / charts / jobs / Explorer models that use it, the related tables (the screens' nested tables,
  clickable) and its columns with the dictionary's labels and the screens showing them
  (a table no screen shows — a dashboard's query — reads its columns from its SQL's select list)
  (`/admin/dev/data`). Configuration: the header counts what is edited in a screen, what stays in
  the file / environment and what is not in the UI, and a status filter replaces the checkbox.
- The Liberty Next workspace (superuser): the framework's pages are no longer listed under every application's menu —
  they form a workspace of their own, picked in the application switcher like an application or with the new button
  of the top bar (which returns to the last application). The Settings page is split: each editor is its own page
  (`/liberty/<page>`, every one with the "reload the configuration" button), grouped by purpose — Build, in the order an
  application is made (screen assistant, connectors, dictionary, screens, menus, actions, charts, dashboards, Explorer
  models, report templates, notification rules, AI, integrity), Administer (app, pools, access, theme), Operate (monitoring, jobs), Deploy (package,
  changes, history), Document (developer reference, documentation reports, release notes). Package, Changes and
  Integrity leave the nomaflow application (liberty-apps 0.1.3). The former `/settings?tab=…` and `/nomaflow/package |
  changes | integrity` links redirect to the new pages; the editors open on the last application used; messages that
  pointed to "Settings → …" name the new place.
- Liberty Next → Document → Documentation reports (superuser): the implementation report and the technical report,
  picked from two cards; the sections to include, the language, the cover's date label, PDF or Markdown (`/admin/dev/reports`). The
  technical report documents the release from the running code (architecture, data model, configuration reference
  with its gaps, API with the access of every route, command lines, packaging, code references); the implementation
  report documents this install (enabled components, applications and menus, screens, connectors and pools with their
  load state, dashboards / charts / Explorer models, jobs, notification rules, roles and users, dictionary, settings
  and API as appendices), secrets masked.
- PDF reports: a table with long identifiers (query names, paths) no longer runs past the right margin —
  the cells wrap them after `_`, `.` or `/`. Developer reference → Configuration and the technical report
  show a default path as `${LIBERTY_APPS_DIR}/…` instead of the absolute path of the server.
- Installable as an app (PWA): Chrome / Edge offer "Install" and Safari "Add to Dock" — Liberty Next opens in its
  own window, named after the instance (the theme's app name). The service worker caches nothing: every request goes
  to the server as in the browser, so a release is used on the next load and no data stays on the device. Needs
  HTTPS (or localhost); works under `[app] base_path`. Icons 192 / 512 px, maskable and Apple touch.
- nomaflow without superuser: `nomaflow:view` follows the jobs (list, schedule, runs with their log and files),
  `job:<id>` (`job:*` for all) runs a job, uploads its file and cancels its runs — the permission a job menu item already
  asks — and `nomaflow:edit` creates, edits, deletes, pauses and resumes jobs and applies the change
  (`POST /admin/jobs/reload`, the jobs part of the reload); edit includes view. The retention / missed-schedule
  policies, their manual runs and the job export stay with a superuser. Granted in Access → Other access → nomaflow; the
  nomaflow menu pages show only to who holds view (or a menu allow), and the nomaflow application appears in the picker
  with its connector (`sql:nomaflow:*`). Editing jobs is a strong grant: their steps run SQL and allow-listed Python
  without row scope. The Run-with-parameters dialog reads only the job run (`GET /admin/jobs/{id}/definition`).
- Module catalogues (catalogue v2, step 1): a `[[modules]]` block beside the presets describes a whole application
  module — one screen per table with its key, grid columns, dialog tabs and detail tabs (the child screen and the key
  it inherits), and the menu folder — columns named by data item, the JDE prefix resolved later from the live schema
  (`liberty/modules.py`: model, loader, validation of the detail graph). Apply a module
  (`/admin/assistant/modules`, `…/{id}/preview`, `…/{id}/apply`, superuser): the preview reads the live tables and
  says, per screen, the prefix found, the columns, the data items the table lacks, what is new and what exists; the
  apply writes the new read queries (`<screen>_get`, ordered by the key), the read-only screens with their detail tabs
  (filtered on the inherited key, hidden), the menu folder and items — additively, what exists is never overwritten,
  the three files validated before any is written — then the dictionary entries of its tables under the app's scope
  (JDE labels, formats, UDC lookups) and a reload. A table or a key column not found blocks it. A detail can be
  one-to-one (`cardinality = "one"`: the child's form as a tab, not a grid); a screen can join a lookup table for a
  few columns (`[[modules.screens.joins]]` — the address's name on a customer list), placed in the module's column
  order; `filters` names the columns of the advanced filter panel (filtered in the database before the rows are
  loaded, joined columns included); `?replace=1` rewrites the module's own queries, screens and menu items in place.
- A `read_only` screen with a dialog opens it on a row again (view-only: fields disabled, no Save / Delete, its
  detail tabs read-only) — the grid only opened the dialog when the screen had an update or insert query.
- Screen Assistant → A module: the module catalogue (search, groups), the preview read on the live tables (per
  screen, as a tree of its details: table, prefix, columns, data items the table lacks, new / exists / replace; the menu;
  what blocks it), Replace with its warning, Apply — the sidebar refreshed, then Open the module's first screen.
- Detail tabs (nested table / form) take their link values from the whole loaded row, not only from the dialog's
  fields — a link column the screen keeps out of the form (a detail's key) was dropped, and the tab then filtered on
  part of its key and showed other records' rows (Who's Who → Email listed every address's line-1 email); a tab whose
  link is incomplete now loads nothing.
- A `read_only` screen takes no record lock (it writes nothing; its dialog showed "Locked by …" to the next reader).
- A read-only lookup field shows its description beside the code, and a grid lookup cell finds its description when
  the stored code is shorter than the JDE UDC key (`"C"` against `"       C"`) — the trim-tolerant match the dropdown
  already applied (`matchOption`).
- A shared grid view that lists a LOOKUP / ENUM column keeps its description column right after it (`<col>__lookup`),
  unless the view places it itself — a view naming the code hid the label the grid shows beside it by default.
- Apply module: the grid's columns go in a default view; the dialog-only columns are no longer `hidden` (the dialog
  dropped a hidden column's field — the Flags / Category codes / Related addresses tabs were empty).
- `open_url` action: open an external address from a row menu, a toolbar button, a dialog tab, a drill column or a
  chain — `url` is a template (`{COLUMN}` from the row / form, prompt fields, chain paths, URL-encoded; `#LOGIN_USER#`
  and the other built-ins; a leading placeholder is the address itself), `new_tab`
  (default on), the prompt-before-fire of `navigate`. Only http(s) addresses and paths of this server are opened —
  refused at load and checked again on the filled URL. A lone `open_url` in a row menu fires from the row handle. A
  `url` menu item opens an address in a new tab (shown unless the menu denies it). The action editors offer it.
- Liberty Next → Administer → App: the `[app]` settings in three groups — Server (name, host, port, log level, hot
  reload, Oracle thick mode, base path), Display (default language and period, page size, dashboard cache, tabs,
  drill-downs) and External addresses; app.toml unchanged. The page lists its sections in alphabetical order
  (of the current language), the read-only deployment list last.
- External addresses (`[app] external_urls`, Liberty Next → Administer → App): named addresses of the systems an
  install links to — `JDE_WEB = "https://jde:8080/jde"` — used as `#URL.JDE_WEB#` at the start of an `open_url` / a
  `url` menu item, so shipped screens carry no customer address; served to signed-in users (`/api/external-urls`),
  live on save. An address the install did not set leaves no URL (refused, never opened as a local path).
- Module catalogues: a screen's `row_menu` (as in screens.toml); an `open_url`'s `{ITEM}` placeholder names a data item
  and becomes the physical column (`{AN8}` → `{ABAN8}`).
- nomaflow job editor: saving a job rewrites only the jobs that changed. The editor receives every job with its
  `${…}` references resolved, and a save wrote them all back — one server's paths (`${LIBERTY_APPS_DIR}/…` turned into
  `/srv/…`) landed in every other job of the file; the unchanged jobs now keep their text as it is on disk (comments,
  `${…}`, `step_defaults`), and the edited job gets its `${…}` references back where the value still matches.

## 7.0.64 — 2026-10-28

- Package & Deploy: an import can be previewed (`/admin/import-package?dry_run=true`): the plan
  lists every entity — new, changed (with its fields before / after), identical, kept (`override
  = true` here), skipped, removed — with warnings when the package comes from a newer framework or
  carries `ENC:` values this instance's master key can't decrypt. An apply returns the same plan.
- Package & Deploy: a package now also carries shared actions, Data Explorer models (with their
  analyses), notification rules, nomaflow jobs and an application's AI starters and playbooks.
  A job that already exists on the target keeps its schedule, enabled flag, timezone, presets,
  alerts, retry, log level and timeout; a new job arrives disabled.
- Package & Deploy: a package is built from the files as written (unset values and `${VAR}`
  references kept), and a connector's queries, tables, sequences, lookups and endpoints are
  imported one by one — importing a package no longer replaces a whole connector (its other
  queries were lost), and a screen's queries ship even when its connector isn't selected.
  `connectors.toml` and `jobs.toml` keep their comments through an import.
- Settings → Package: Import previews the package as soon as it is picked (and again when the
  strategy changes): counts per state, the warnings, then each file's entities — what changes
  first, identical ones on demand — and a changed entity opens on its fields, here now / after the
  import; "Apply N change(s)" then writes it and shows the result. Build offers the new kinds:
  Explorer models, nomaflow jobs, notification rules, shared actions, the assistant per application.
- Promotion to another instance (API; the screens follow): `[promotion.targets.<name>]` in
  app.toml — the target's URL (with its sub-path) and a local superuser account there, the password
  stored encrypted (`/admin/promotion/targets`, a sign-in test per target). `POST
  /admin/promotion/push` builds a config package (the Package selection; connectors, pools and API
  endpoints stay the target's unless picked) or takes an approved change package, signs in on the
  target and returns its preview — or applies it there. An applied promotion is kept in a history
  (`<config>/.promotion/`, `/admin/promotion/history`) and marks the change package exported.
- "Promote to…" on Settings → Package (Build) and on an approved change package (nomaflow →
  Changes): pick a target instance — it previews at once (the package's plan with its diffs, or the
  change package's report where a conflict or an unverifiable change can be forced) — then "Apply on
  <target>". Package → Promotion edits the target instances (Test signs in there) and lists the
  promotions applied from here; a row opens on the target's report.
- Changes → Apply: a change whose drift can't be verified (no read query) can be forced — it never
  applied before, the force box only showed on conflicts.
- Deployment: the database can live on a PostgreSQL server outside the stack. At install,
  `./liberty install full --db external --db-host H [--db-port P] [--db-user U] [--db-name D]
  [--db-password-env VAR]` starts no bundled Postgres; later, `./liberty db external --host H …`
  moves an install (the app's default pool rewritten, the pools that named the bundled server —
  the apps' — moved, the bundled Postgres stopped with its volume kept) and `./liberty db
  embedded` comes back. Recorded in the compose chain (`docker-compose.external-db.yml`,
  `docker-compose.no-embedded-db.yml`) and `.env`, so start / upgrade / backup follow it;
  `./liberty db dump` takes a `pg_dumpall` of the bundled Postgres to carry the data over;
  `status`, `doctor` and `backup` show where the database is. New `liberty-admin set-default-pool`
  and `retarget-pools`.

## 7.0.63 — 2026-10-28

- Monitoring → Server log: the server log is now kept on disk — a size-rotated JSON-lines file
  (`[app] log_file`, default `<config dir>/.logs/liberty.log`; `log_file_max_mb` 10,
  `log_file_backups` 5) fed by the framework, the apps and the libraries' warnings, whatever
  launches the server; a burst of the same message is written once with "repeated N times". The
  page searches it (level, module, text in the message / logger / exception / request id,
  period), opens an exception, filters on one request, exports the matches as text and downloads
  the files; the levels per module change at once and are saved to `[app] log_levels`
  (`/admin/logs`, superuser). The live view stays as the first tab.
- Monitoring → Retention: every store the server keeps growing in one place — job runs, job
  files, notifications, config versions, change packages, saved views, the server log — with
  what it keeps, where that is set, its size, a Preview (a dry run: what would go, nothing
  deleted) and Run now (`/admin/retention`, superuser). Change packages get a policy:
  `[changesets] retention_days` (0 = keep, the default) deletes exported / promoted packages and
  import logs older than that, checked with the notifications retention every six hours; drafts
  are never touched. Saved views are the users' own work: shown, never purged.
- Sidebar: "Open on hover" — the menu folds to its icons and floats over the page while the mouse
  is on it (picking a page folds it back); "Keep open" returns to the fixed menu; remembered per
  browser.
- Menus: a start page per role — `home_by_role = { auditor = "<menu item>" }` on an app's
  connector: a user lands on the item of their first role listed there, else on `home`.
- An installable web app manifest (`/manifest.webmanifest`, public) named after the instance (the
  theme's app name, else `[app] name`).
- Profile: no Security tab for a single-sign-on session (no local password).
- Dropdowns: options with a group sit under a header per group — the Data Explorer's models per
  preset module ("JDE · Accounts Payable": a preset's `group`, or a model's own `group`), else per
  application (the closed picker shows the group too), a data-scope rule's columns per
  connector; an optional × clears; a compact size. A multi-value dropdown (chips on the trigger, "Select shown", Clear),
  first used for a notification rule's jobs.
- A placeholder picker beside templated fields: the { } button lists the tokens and inserts the
  one picked at the caret — a notification rule's subject, body and link (the job or screen
  event's fields), a notify step's subject and body.
- Grid column filters: a coded column (an enum) picks several values at once and takes a code
  typed beyond its list; a lookup column keeps its single picker. A saved view holding one value
  still opens.
- Grids: `wrap = true` on a screen column wraps long text over several lines instead of cutting
  it; a screen's `page_size` sets its grid's rows per page (else `[app] default_page_size`); the
  rows-per-page picker is the themed dropdown.
- Views menu: "Reset to shipped" brings the grid back to the screen's default shared view, else its
  base layout.
- Serving under a sub-path behind a reverse proxy: `[app] base_path = "/liberty"` (restart) — the
  page, its assets, the API, downloads, the live socket and the links resolve under it, whether
  the proxy strips the prefix or not; the URLs the server builds (the single sign-on callback)
  carry it. Every download and upload now goes through the API client (the session refresh
  applies).
- Fix: nomaflow → Jobs → Export downloads the job's package again (it called a wrong address
  without the session and did nothing).
- Monitoring, Settings → Package and Settings → Access pick their views from a row of cards — an
  icon, the name and one line on what the view is for — instead of plain tabs.

## 7.0.62 — 2026-10-28

- nomaflow: the spreadsheet / CSV uploaded for a run can be downloaded again — under the
  `file_load` step on Run detail, and on the user's job page ("File loaded"). Uploads are kept in
  the job's work area; `GET /admin/jobs/runs/{run}/inputs/{step}` and
  `GET /api/jobs/{job}/runs/{run}/inputs/{step}` serve them.
- nomaflow: the retention sweep now also deletes the job work area's old files — uploads, rejects
  reports, reports, exports, and the file_load watched folders' `done/` / `failed/` archives —
  older than `[meta.retention] files_days` (blank = the runs' `days`, 30 by default; 0 = keep the
  files). **On upgrade, files older than 30 days go at the first sweep** unless `files_days` is
  set; an export's copy in its `export_dir` is never touched. Set it in the Retention panel; the
  sweep reports the files and bytes deleted. Saving the retention policy no longer fails on a
  blank field.

## 7.0.61 — 2026-10-28

- Jobs: a plugin report step (`report:<scope>:<id>`) takes the run's job params and user
  parameters on top of its own `params`, typed by the report's declared parameters.
- Dashboards: the page grows and scrolls to its end; a full-width links card shows all its links;
  more sidebar icons for the app menus.
- Jobs: a user parameter without `step` is a job-level parameter, passed to every step; its
  choices can come from a read query (`choices_from = "query:<connector>/<query>:<value>:<label>"`).
  The admin "Run with parameters" dialog offers the same dropdowns.
- nomaflow: the jobs list is searchable (id, description, tag), filtered by application, status
  and tag (kept per browser), grouped by application with collapsible groups, one line per job
  with icon actions.
- Jobs: a `job` menu item can pin one of the job's presets (`params = { preset = "…" }`); the
  user's page runs it with that preset's parameters and step switches.
- nomaflow: the Schedule page lists the upcoming runs day by day with the schedule in words
  ("every day at 03:00"), then the schedules switched off; the jobs run by hand only are folded
  away. No more "Back to jobs" button. The sidebar no longer highlights Jobs on the Schedule page.
- nomaflow: picking a preset in "Run with parameters" starts from the job's saved values, as the
  scheduler does; it no longer keeps the steps the previous preset switched off. Jobs tagged
  `main` are listed first, in a "Main jobs" group.
- Config merge: `merge_jobs` / `merge_jobs_file` merge a shipped `jobs.toml` per job — the
  product's steps refreshed, the operator's fields (enabled, schedule, timezone, presets, alerts,
  retry, log level, timeout, parameter values) kept; a job gets an `override` flag. Used by the
  apps installer.
- Jobs: `for_each = "<param>"` runs a job once per value when that parameter holds several (a
  list, `"1,3"`, or `"all"` = every choice of the user parameter of that name), one run after
  another, each with a single value; a scheduled fire or preset with `"all"` covers every value.
  A user parameter with `multiple = true` shows as checkboxes with "All", on the user's page and
  in "Run with parameters".
- nomaflow: a Job Runs page (`/nomaflow/runs`) replaces the generic grid: search, job, period
  and state filters (the state chips count the runs), runs grouped by day with their parameters
  (the application of a per-application run), trigger, duration, rows, the error under a failed
  run and a progress bar while one runs; it refreshes itself while a run is going. Backed by
  `GET /admin/jobs/runs`. The Run detail page loses its back buttons.
- Sidebar: a `shieldalert` menu icon.
- Connectors: a field may hold an `${ENV}` reference even when it is a boolean or a number
  (`verify_ssl = "${JDE_AIS_VERIFY_SSL:-true}"`): saving connectors in Settings, the delete with
  dependencies, the scaffold and the API test now validate the values as the runtime resolves
  them (they refused the file before). The API connector editor shows and keeps such a reference
  for the TLS check.
- Config merge (apps upgrade, Package & Deploy): a shipped value that is a whole `${VAR}` or
  `${VAR:-default}` reference belongs to the install — the live value wins, booleans, numbers and
  the install's own reference included (several AIS connections, each on its own variable). Before, a TLS check switched off in Settings was switched back on.
- Jobs: a deadline — `alerts.finish_by = "07:00"` (the job's timezone). On a day the job is
  scheduled, a successful run must have finished by then; otherwise the new `late` job event goes
  to the notification rules and the job's alert recipients, saying whether the run is still going,
  failed or never started. Checked with the missed schedules; set in the job editor (Alerts).
- Dashboards: chart and table widgets carry their title and description inside their card, like
  the other widgets.
- nomaflow: a reload no longer leaves the "All" of a per-value run on the previous connectors.
- Dashboards: a `jobs` widget — the nomaflow jobs (the ids listed, or every scheduled job, by
  tag) with the next fire or "paused", the schedule in words, the last state, and Run now /
  Pause / Resume. Superusers only (dropped for anyone else); the dashboard editor offers it.
- nomaflow: pausing or resuming a job (`POST /admin/jobs/{id}/enabled`) edits that job in
  jobs.toml and re-registers its schedule, without a full reload; the Jobs page uses it too. The
  job editor's Alerts block is laid out on a grid again.
- Jobs: a `file_export` step — a named read query written to an xlsx or csv file in the run's
  work area (the `notify` step attaches it, Run detail downloads it), optionally copied to
  `export_dir`. Binds from the run parameters and templated `params`; the file name and folder
  take `${params.x}` / `${run.id}`; the rows stream from the database, headers are the column
  labels. A form in the step editor.
- nomaflow: on a user's job page, the Run button lines up with the fields.

## 7.0.60 — 2026-10-28

**Data Explorer: models shipped by the presets, dictionary entries in one click**
- A Screen Assistant preset carrying `app` + `explorer = true` ships a Data Explorer model by
  default (`explorer.<app>.<preset id>`): listed with the others, built from the preset at first
  use, never written to `explorer.toml`. Declare a model on the same preset to customise it. A
  preset table takes a `label` (the entity label) and a `cardinality` (`one` for a lookup joined to
  a transaction hub: a plain LEFT JOIN).
- The JDE catalogue ships eight new preset files — General Accounting, Accounts Payable, Accounts
  Receivable, Inventory, Sales, Manufacturing, Product Data Management, Product Costing — from the
  Oracle E1 data models; the fifteen "full" presets (Address Book included) ship their model.
- Applying a preset in Settings → Explorer creates the dictionary entries its tables lack (the
  assistant's Dictionary step, filed under the app: a JDE source gets its data dictionary
  descriptions). On the Explorer page a superuser sees how many columns show their raw name and
  creates the entries in one click (`POST /admin/explorer/{app}/{model}/dictionary`).

**JDE amounts: the data dictionary's decimals**
- New dictionary format `jdenumber` with a `decimals` entry field: a JD Edwards amount stored
  without its decimal point (F9210.FRCDEC) reads as the real value, writes back as the stored
  integer, and filters / explorer bounds are bound in real units; explorer sums and detail
  columns are scaled in SQL, the result columns carry `decimals` and the Explorer table shows
  exactly that many places. `decimals` on the other numeric formats is the places shown.
- The dictionary scan (the assistant, the standalone page, the explorer preset path) reads
  `DD_DECIMALS` from the per-table JDE query; the review tables get a Decimals column. The apps'
  `dictionary_data_items_for_table_get` now emits `jdenumber` / `jdedate` / `integer` types,
  the decimals, the size and the justify rule next to the UDC lookup rule.

**Jobs for users: a menu item that runs a job and shows its log**
- Menu item type `job` (target = the nomaflow job id, permission `job:<id>`, `!job:<id>` hides
  it): the leaf opens the job's own page — the description, one file picker per `file_load`
  step, Run, the run's log followed live, the previous runs with their state, duration and
  files. Routes under `/api/jobs/{id}` (the job, upload, run with the uploaded files only, a
  run's detail and files); nothing else of the nomaflow control plane is exposed. Settings →
  Menus offers the jobs as targets; Settings → Access has a Job rule family.
- The apps ship the menus: nomademo gets an Import folder (items, companies, a month of sales,
  refresh periods), nomasx1 a Jobs folder (collect security, collect licences), nomajde a Jobs
  folder (the two security re-merges).
- The nomaflow Overview dashboard takes a period (last 7 days by default): the figures, the
  runs per day, what needs attention, the runs and durations per job and the recent runs
  follow it; "In flight" stays live.
- The dictionary refresh of the Explorer models is also a job: `liberty.jobs.dictionary:
  refresh_model_dictionary` (`app`, optional `models`) scans every table of the app's models,
  writes the missing entries and rebuilds the connectors, with the tables and entries in the
  run's log. Python job steps receive the live runtime as `app_state` when they declare it.
  nomajde ships it as `nomajde-dictionary-refresh` under its Jobs menu.
- A job declares what a user may set when running it from its page: `[[jobs.user_params]]`
  (`name`, `label` + `l`, `step`, `default`, `required`, fixed `choices` or
  `choices_from = "explorer_models:<app>"`). The page shows a dropdown or a text input per
  parameter; the value reaches the step's parameters, anything else is refused. The nomajde
  dictionary refresh takes the Explorer model to refresh (blank = every model).

**Dashboards: text and links widgets — an overview for a management application**
- `text`: a block of Markdown (what the application is for, how to read the page); `links`: a
  card of entry points, each a menu item of the dashboard's app (shown only when the user can
  open it, under the item's label by default) or a URL. Both in the widget editor, both
  localisable, both exported (the text as it is, the links as a table).
- nomajde gets its Overview: what Nomajde does, the live figures of the JDE instance (users,
  roles, security records, UDC codes, objects) and the entry points of each domain; the app
  opens on it.

**AI assistant: the Data Explorer as tools**
- `explorer_models`, `explorer_fields` and `explorer_query` let the assistant answer a total,
  a ranking, a trend or a comparison through the Explorer models (dimensions, measures,
  filters, period, sort, Top N) under the caller's scope, instead of declaring that no
  aggregate query exists. On by default (`[ai] explorer_tools = false` to remove them).
- The assistant also saves what the user asks for: `explorer_saved`, `explorer_save_analysis` (the
  definition is run once before it is stored, private unless shared) and `explorer_save_dashboard`
  (tiles of saved analyses). The Explorer page publishes its context to the chat (the model, the
  analysis on the canvas), and `[ai.<app>.screens.explorer]` starters show there.

**Workspace tabs: an operator policy** (`[app]` in app.toml, Settings → App)
- `tabs_multi` (off: one page at a time, opening another replaces it), `tabs_close_on_app_switch`
  (the tabs close when the operator picks another application, the new home kept) and `tabs_max`
  (default 5; the oldest tab closes when the limit is reached).

**Grid: a visible handle for the row menu**
- Every row of a screen with `row_menu` actions carries a quiet handle in its first column (so it
  stays in view on a wide table), brighter on hover: a lone `navigate` action fires at once (the
  handle shows its label), several actions open the same menu as the right-click. Nothing else
  in the drill mechanics changes.
- In-place drill, opt-in: a `navigate` action with `inline = true` opens its target (a query or a
  screen, with the row's binds) as a panel inside the current tab, over the grid it came from, with
  a breadcrumb and a Back button; the grid behind keeps its rows and filters, and a drill from the
  panel nests the same way. `[app] drill_inline` (Settings → App) sets the default for every
  drill whose action does not say; an action's own `inline` overrides it either way. Off by
  default, so nothing changes until the operator turns it on.
- Link-style cells: a column hint `drill = "<row-menu action id>"` renders the value as a link
  that fires that `navigate` action for the row — the key column is the drill, no handle or menu
  needed. Automatic too: a screen with a single `navigate` action and a key column gets the
  link on its first key column without any setting. Settings → Screens offers the screen's
  row-menu actions in the field; a wrong id is refused at load.
- Dashboards: a list row's tag sits on the title line, so a row stays two lines high.

## 7.0.59 — 2026-10-28

**Fixes from the documentation review (2026-10-27)**
- Dialog: a `required` field, or a `required_when` that holds, now blocks Save when empty (the
  asterisk was only decorative).
- Dialog → Duplicate: the related 1:1 rows (column groups) are inserted under the new key. Since
  June the copy re-seeded the source row behind the form, so the group ran an UPDATE on the new
  key (0 rows, silently) instead of its INSERT; the source key also reappeared in the form.
- Settings → Menus: a `dashboard` item's target dropdown lists the dashboards (by qualified id,
  the menu's own app first); it offered nothing.
- Parameters: a declared default or a supplied value reaches its bind whatever the case
  (`status` declared, `:STATUS` in the SQL).
- nomaflow: a cloned job's presets lose their schedules (they fired twice: the scheduler runs a
  preset's cron even on a disabled job); a job's `alerts.on_long_run_minutes` now sends the long-run
  message to its `alerts.recipients`.
- Notifications: `[notify] retention_days` purges read notifications (at boot and every six hours);
  the e-mail carries the rule's link; a rule recipient without prefix that is an address is sent to
  that address, another bare word is logged instead of silently dropped.
- Screen Assistant: the generated `_post` / `_put` write the JDE audit items (UPMJ, UPMT, USER,
  MUSE, PID, JOBN; never a key column) as `{{JDEDATE}}` / `{{JDETIME}}` / `{{LOGIN}}` / `{{PID}}` /
  `{{JOBN}}` tokens, the convention of the shipped queries — a bound audit column kept the value the
  dialog sent back on an update.
- `liberty-admin create-role` on an existing role keeps its permissions when `--permission` is not
  given; `set-scope` refuses to run without `--rule` or `--clear` (it silently cleared the scope).
- Settings → App: changing `oracle_thick` reports that a restart is needed (thick mode is set once
  per process). The Chart view's "Save…" uses the one chart id rule.
- The writable-query confirm showed the literal "table.run". The `ColumnHint.key` and
  `Screen.actions` descriptions, the connector's SYSDATE comment and the `liberty-admin` docstring
  say what the code does. `release/README.md`, `install.sh` and the Traefik config no longer
  mention `--license-key`, a read-only apps mount, `admin/admin` or `reset-admin-password`.

**AI assistant: the page context (C6)**
- The assistant now sees what you are looking at. While a data screen is the active tab, the
  chat carries its context with every message: the app, the screen (connector and read query),
  the active filters, the loaded rows' count and columns, and the row you last clicked or
  opened the row menu on. "This screen", "these rows" or "the selected row" resolve without
  retyping names; the query names in the context are the real ones, so the assistant can run
  them. A chip above the composer shows what is sent; its × drops it for that page. The context
  rides as a second, uncached system block capped at 4 000 characters, so the cached prompt and
  tool list are untouched (`POST /ai/chat` takes an optional `context`).
- **Starter questions, a model picker, a conversation that stays.** Settings → App → AI
  Assistant gains `starters`: questions shown on an empty conversation, everywhere (`*`), per
  app or per `app/screen` (the most specific set for the page wins; a click sends the question),
  and `allowed_models`: the models a user may pick in the chat besides the configured one (the
  picker shows only when there is a choice; the server refuses any other id). The conversation
  now survives closing the drawer and a reload (the last 40 messages, on this device); "new
  conversation" clears it. The App editor's dropdowns (model, effort, log level, the allowed
  connectors) are the shared searchable select; the model list adds the Claude 5 family.
- **The applications ship their own starter questions.** A new per-app file, `ai.toml`
  (`[ai.<app>] starters`, `[ai.<app>.screens.<id>] starters`), travels with the apps bundle like
  the other per-app files (merged on install, hot-reloaded, versioned) and is edited under
  Settings → AI. The operator's questions under App → AI Assistant win over it, key by key.
- **Playbooks, and an assistant that knows the installation.** `ai.toml` also carries
  `[ai.<app>.playbooks.<id>]` (a title, an optional description, the procedure in Markdown),
  edited under Settings → AI. When a user asks how to do something in an application, the
  assistant lists the playbooks, loads the one that fits and follows its steps, saying which one.
  A new `describe_instance` tool summarises what this installation holds (applications, menus,
  screens, dashboards, explorer models, jobs, playbooks), so "where do I find…" is answered from
  the real configuration. A tool result above 60 000 characters is cut with a note asking the
  assistant to narrow the call.
- **Operations tools** (`[ai] ops_tools`, off by default, a checkbox under App → AI Assistant):
  `jobs_status` (the nomaflow jobs with schedule, last run and next fire; one job's recent runs
  with the failing step), `server_log` (the recent log lines, by level and text) and
  `integrity_checks` (the configuration cross-reference checks). The assistant reads the running
  server instead of guessing when something failed, is late or is missing. Off by default because
  the log can carry what the server logged.
- **The Explorer's PDF is a synthesis, with its charts.** An analysis or a dashboard exported to
  PDF (from the page or by a `report` job step) now reads like nomaubl's reports: a cover with
  the application, the period, the date, the author and the version, then the sections flowing
  one after the other (no contents page, no page per section). Every chart is drawn
  server-side from the section's data (`liberty/explorer/chart_svg.py`: bar, grouped, stacked or
  horizontal, line, pie or donut) in print colours with a legend, compact axis ticks and the
  values on the bars — the same figure whether a user exports or a job renders a digest. KPIs
  become cards; the numbers follow the reader's language (`1 234,5` / `1,234.5`); the
  template's own words (cover labels, confidential lines) follow it too. Excel keeps the data
  only.
- **Every UI string in both languages (C10).** The 870-odd strings the newer builders carried
  only as inline English defaults (`t('key', 'Default')` and `defaultValue:` options) now live
  in `locales/en.ts` and `locales/fr.ts`, translated; the lint forbids a new inline default so
  the French file never silently misses a key. The assistant's starter questions take a
  language block in `ai.toml` (`[ai.<app>.l.fr]` with `starters`, `groups`, `screens`), served
  by `/ai/tools` in the request language; the three apps ship their French starters.
- **Settings → Dashboards says which widgets a filter reaches.** Under each filter, the editor
  lists the widgets whose screen carries the dictionary key or whose result has a column named
  like it (the dictionary bind), and the ones it misses.
- **Dashboards in the reader's language.** A dashboard, a filter and a widget take an `l`
  block of per-language overrides for their texts — `l.fr = { label = "…", description = "…",
  sub = "…", count_label = "…" }` — including the column a list reads a text from
  (`title_column = "TITLE_FR"`), so a query can carry a French column next to the English one.
  The routes and the export apply the block of the request language (`X-Liberty-Lang`,
  region stripped); the block itself never travels. The Explorer PDF's note under a section
  (period, filters, groups) follows the language too. The widget and chart editors show a
  configured column in the sample's spelling, so an upper-case name in the TOML matches the
  lower-case column Postgres returns.
- **Explorer: the pickers follow the model.** Switching models quickly (the first model, then
  the current app's) could leave another model's saved analyses in the pickers: a response for a
  model the page has left is now dropped, and a failed list empties the pickers. After a deploy
  replaces the bundle, a page that fails to load a lazy chunk reloads itself once instead of
  showing "Failed to fetch dynamically imported module".
- **Charts pivot on a column.** A chart spec takes a `series` column: one series per distinct
  value of it (a process, a status…), each the aggregate of the first `y` column — conflicts
  per refresh stacked by process from a three-column query, no pivot in SQL. In the chart
  editors as "Pivot by"; the dashboard export draws the same pivot. The widget editor dialog
  edits the three synthesis kinds (figure, list, bars) with their columns, formats and a live
  preview, and every widget's subtitle; a PDF card shows its sub line in small type under the
  figure.
- **Synthesis widgets for the dashboards.** Three widget kinds for a synthesis page like
  nomaubl's billing overview, all configuration: `stat` (a figure card from the query's first
  row: the value formatted with a unit, a sub line templated over the row — `"{ENABLED:integer}
  enabled · {DISABLED:integer} disabled"`), `list` (a decision or ranked list: title, subtitle,
  figure, count, a tone dot ok / warn / error / info, a tag pill) and `bars` (a ranked list with
  a proportion bar per row, as a share of the total or of the largest). Every widget takes a
  `description` subtitle. The card widgets carry their title inside the card; the dashboard
  export shows a stat as a card and a list or bars as a table. The visual editor adds the three
  kinds with their preview.
- **A menu link opens the Explorer on one model.** A `page` menu item may target
  `/explorer/<app>/<model>`: the tab opens the Data Explorer on that model (the picker still
  lets the user switch). The apps bundle uses it to put a "Data Explorer" entry in each domain
  folder (nomasx1: Security, Conflicts, Licenses; nomajde: Security, UDC, Development).
- **Dashboard filters and period on a query without a screen.** A dashboard filter (and the
  period's date column given as a dictionary item) used to bind only through a widget screen's
  `dd` map; a widget on a plain query ignored it. The page now sends the pick as a *dictionary
  bind* (`_dd_<ITEM>`, `_dd_<ITEM>_from` / `_to`) and the SQL connector binds it on the result
  column carrying the item — found as the data scope finds its columns: a hint's `dd`, the
  column's own name, or the name minus the connector's `dd_prefix` (`ABCO` → `CO`), from a
  one-time probe. An item no column carries is ignored, as before. The export does the same.
- **Dashboards export to Excel and PDF.** A config dashboard (`dashboards.toml`) has Excel and
  PDF buttons in its bar, next to Refresh. The export runs server-side with exactly what the bar
  holds — the filter picks bound through each widget's screen, the period's binds and date range,
  under the caller's row-level scope (`POST /api/dashboards/{id}/export`) — and goes through the
  same renderers as the Explorer: one sheet per widget (the KPIs together as "Key figures"), or a
  PDF synthesis whose cover names the period and the filters, with the charts drawn and the KPIs as
  cards. A widget whose query fails becomes an error section; the rest still renders.
- **The chat drawer on nomaubl's layout**: a header line (title, the model picker when there is a
  choice, "+" new conversation, ×), a context line under it when the page gives one, a greeting
  bubble, and the starter questions as chips above the composer, under headings when the app
  ships them (`ai.toml` `groups`, per app or per screen); the follow-ups take the chips' place
  after a reply. The dashboard's KPI card carries its label inside, the figure left-aligned, the
  detail under it, as nomaubl's.
- The chat drawer's top line keeps only what is actionable (the model picker when there is a
  choice, "new conversation"); a table in a reply scrolls sideways in a smaller font with each
  figure on one line. The assistant knows today's date (an uncached line with the page context),
  so "last month" no longer needs a guess.
- **Follow-up chips.** The assistant ends each reply with two or three questions the user is
  likely to ask next; the chat shows them as chips under the reply (a click sends one) and never
  shows the marker line the model wrote.
- The chat folds a turn's tool calls into one muted line ("3 tool calls", open to see them); a
  failed call keeps its message visible.
- Dropdowns: a long code (a model id) is no longer cut short in the list.
- A query ordered by an alias-qualified column (`ORDER BY s.company_name`) failed with "missing
  FROM-clause entry" as soon as a filter or a data scope wrapped it: the hoisted ORDER BY now
  drops the qualifiers.
- Data scope: a rule's values are bound as the dictionary item's type even when the result
  column comes back lower-cased (Postgres), so a company code scoped from a user attribute no
  longer fails with "integer = character varying" on a screen or in the Explorer.
- nomaflow `file_load`: with `partition_by`, the refuse query runs once per partition the file
  carries, with the partition columns as binds (`:YEAR`, `:MONTH`…), so a closed-period guard
  follows the file rather than a typed parameter.
- nomaflow: a step's database error reads as the driver's sentence ("invalid input for query argument
  $1: 2025 (expected str, got int)") instead of the exception class, dialect prefix, statement and
  bound values. The statement still goes to the server log.
- Charts: the Y axis shows compact ticks (1.2k, 27.5M) in a wider gutter; a large value was cut
  on the left ("0000" of "20,000,000"). The tooltip keeps the full number.
- Data Explorer: a chart in a dashboard tile (and on the page) stayed an empty frame when the
  layout settled after the chart measured its height; the chart now draws at the measured height.
- Data Explorer: a table entity's fields now carry their dictionary labels. The items were looked
  up under the data connector (`jdedwards`) while the dictionary files them under the app
  (`nomajde`), as the screens do; and a field with no label showed its lower-cased name next to
  its upper-cased one.

**Install / upgrade**
- **Fixed: the apps bundle version read as "not installed" on Docker.** The wheel is materialised
  into the `/apps` mount, not installed as a package in the running container, so `liberty status`,
  the upgrade report and the server's own upgrade journal never saw it. The version now comes from
  the bundle manifest the installer writes (`.bundled/manifest.json`), with the package metadata as
  the fallback on pipx installs.
- **New `liberty-admin migrate --app X --stamp REV`** — sets an app's Alembic version without running
  anything, then applies what became pending (`--dry-run` lists it). The repair for a database whose
  version table is ahead of its real schema. (Cause fixed on the apps side: `init_schema` used to
  stamp head on an existing database, marking revisions as applied that never ran — it now stamps
  only a fresh database.)
- The upgrade report's "Image tag" no longer shows "-" for an unpinned install.

**Notifications (first part): the inbox and the dispatcher**
- A notifications inbox (`ly2_notification` on `[notify] pool`, created at startup like the other
  framework tables) and a dispatcher with three channels: **portal** (an inbox row per recipient,
  pushed at once to the user's Socket.IO room), **email** (SMTP from `[notify]` in `app.toml`:
  host, port, user, an `ENC:` password, from, STARTTLS or implicit TLS) and **action** (a
  connector query or endpoint run with the message's values, for a webhook or a log table).
  Recipients: `user:<name>`, `role:<name>` (every active holder), `email:<address>`. Subject,
  body and link take `{placeholders}` from the event's values. A channel that fails is logged and
  reported, never blocks the others. Routes: `GET /api/notifications` (`?unread=1`),
  `GET /api/notifications/unread_count`, `POST /api/notifications/read`, and for a superuser
  `POST /admin/notify/test` to try the channels.
- **Rules** in `notifications.toml` (`[notifications.<id>]`, hot-reloaded): a trigger, the
  channels, the recipients, a subject, a body and a link with `{placeholders}`. Three triggers.
  A **screen** event: an insert, update or delete through a screen, with an optional `when` on
  the written row in the `field = value` shape ("STATUS = closed"); the placeholders are the
  row's columns plus `USER`, `APP`, `SCREEN`, `EVENT`. A **job** event: `failure`, `succeeded`,
  `canceled`, or `long_run` with `long_run_minutes`, for every job or a list; placeholders `JOB`,
  `RUN_ID`, `STATE`, `ERROR`, `DURATION_MINUTES`, `ROWS`. A job's own `alerts.recipients` get a
  failure message even without a rule. An **action**: a new `send_notification` step in a
  screen's chains fires the rule server-side with the row's values; only a rule declared with
  `kind = "action"` can be fired that way, and the rule keeps the channels and recipients.
  Dispatch runs on its own task, so a write or a run never waits on a mail server.
- **The UI.** A bell in the header with the unread count, updated live through Socket.IO, and a
  dropdown of the latest notifications; a Notifications page (all / unread, mark all read; a row
  opens its link); Settings → App gets a Notifications section for the SMTP account (host, port,
  from, user, password encrypted at rest, STARTTLS or implicit TLS, retention); Settings →
  Notifications edits the rules: the trigger (a screen event with app, screen, events and the
  row condition; a job event with jobs, events and the long-run threshold; or "fired by a screen
  action"), the channels, the recipients picked from users and roles or typed as addresses, the
  subject / body / link templates, the action channel's target, and a "Send a test" button that
  dispatches the rule with sample values.
- **Missed schedules.** The scheduler keeps its cron triggers in memory, so a fire that falls
  while the server is down is never known, and a fire that lands while the previous run of the
  same job is still running is skipped: both left a silent hole in the run history. A periodic
  check (`[meta.missed]` in `jobs.toml`: `enabled`, `grace_minutes` 10, `check_interval_minutes`
  5, `lookback_hours` 24) now lists the instants each active trigger should have fired since the
  last check and looks for a run near each one; a gap is recorded as a **`MISSED`** run (visible in
  the Runs list and as the job's last run; recorded once, a restart never reports it twice) and
  raised as the new **`missed`** job event for the rules, with the `EXPECTED_AT` placeholder. A
  dropped concurrent fire is recorded the same way. A manual run started within the grace counts
  for the fire; a job that never ran is checked from the scheduler's start only. On the Jobs page
  a "Missed schedules" panel next to the retention one edits the policy, shows the last check and
  runs one ("Check now"); routes `GET /admin/jobs/missed`, `POST /admin/jobs/missed/check`.

**Data Explorer (first part): the model**
- `explorer.toml`, several models per app (`[explorer.<app>.<model>]` — one data mart each, tables that
  do not relate are different models): a **hub** entity and the entities around
  it, each a parameterless query of a connector with a `cardinality` (one row per hub row, or
  many), a `key`, a `default_date`, `measures` (column → default aggregate) and `hidden` columns;
  `relations` join entities on column pairs (`on = [["COMPANY", "COMPANY"]]`, any key, one or
  more hops to the hub). No SQL in the file: the **fields** are discovered from each entity
  query's result columns at request time, labelled, formatted and typed through the dictionary
  and the entity's `screen` column hints (a LOOKUP or ENUM rule becomes the field's reference
  list), classified as `string` / `number` / `date` / `datetime` / `boolean` and as dimension or
  measure; field names are upper-cased (`sales.QTY` on Postgres as on Oracle, the framework's
  convention) so an analysis moves between dialects. `GET /api/explorer` lists the models the caller may use (new permission
  `explorer:<app>:<model>`, `explorer:<app>:*` for all), `GET /api/explorer/<app>/<model>/model` returns the model with its fields
  (`?refresh=1` bypasses the per-app cache, which every reload empties); a broken entity is
  reported on the model, not raised. Settings → Explorer edits the model: the hub, the entities
  (the entity editor discovers the query's columns and lets the operator tick the key, the
  measures with their aggregate, the hidden ones and the default date) and the relations;
  `GET/PUT /admin/config/explorer/parsed`, hot-reloaded, part of the apps bundle merge. The
  Access editor offers "Data Explorer" as a rule type.
- **The engine (aggregate mode).** `POST /api/explorer/<app>/query` takes rows (dimensions, a date
  one with a `bucket` day / week / month / quarter / year), measures (`sum` / `avg` / `min` /
  `max` / `count` / `count_distinct` of a numeric field, or `count(*)`), a flat filter bag
  (`key`, `key__op`, `key__from` / `key__to`, a list = IN; operators by type), a period on a date
  field, a sort and a Top N, and runs **one** `SELECT dims, aggs … GROUP BY` that is the same on
  Oracle, Postgres and SQLite: the row sources are the primary entity (the hub, or `entity`) plus
  the entities of the dimensions, at most one of them many-side so rows never multiply twice;
  one-side entities are LEFT JOINed through the relations (any number of hops); a many-side
  entity that is not among the rows is reached by a correlated sub-query for a measure and by
  `EXISTS` for a filter. Every entity query runs with the caller's row-level scope applied inside
  it (`SQLConnector.scoped_select`). SQL groups at day level; Python folds the dates into the
  bucket, merges the groups, computes `avg` from sum + count, sorts and cuts to the limit
  (`groups`, `scanned`, `truncated` in the reply). A request the model cannot serve is a 422 that
  says why; `debug = true` returns the SQL and binds to a superuser.
- **Detail mode, drill, calculated fields.** `POST /api/explorer/<app>/detail` returns the rows
  themselves — the columns asked for (any entity, same join rules) or the primary entity's
  visible fields — with the same filters and period, a sort, `total` and a page (`page` /
  `page_size`, up to 5 000); `drill` takes an aggregate cell's dimension values and turns them
  into filters (a month label becomes the month's day range, a null group the empty rows), so
  the rows behind a figure are one call away. **Calculated fields** are declared in the request
  (`calculated = [{name, label, expr}]`) and used as `calc.<name>` in rows, measures, filters and
  columns: a small typed language (`REVENUE / QTY`, `QTY >= 4 ? 'big' : 'small'`, `company.REGION
  = 'EU'`, `year(SOLD_ON)`, `days(ORDERED, SHIPPED)`, `round`, `abs`, `coalesce`, `upper` /
  `lower` / `length` / `concat`, `in (…)`, `is empty`, `and` / `or` / `not`) compiled to the
  pool's dialect inside the same statement, never free SQL; a division is real and guarded by
  `NULLIF`, a boolean renders `Y` / `N`, a JDE Julian date is converted before a date function. A
  numeric result is a measure, any other a dimension; a type error names the position.
  `POST /api/explorer/<app>/calc/test` compiles one and samples eight values — the editor's
  "Test" before "Save".
- **The page (first part).** A Data Explorer page (`/explorer`, the compass in the header when the
  caller has a model; a `page` menu item can point at it too) with the model's **fields pane**
  (entities with their 1..1 / 1..n badge, dimensions then Σ measures, a search; a click adds the
  field, the funnel adds it as a filter, a drag drops it on a shelf; double-click makes an entity
  the analysed one), the **shelves** — Rows (a date with its bucket), Measures (an aggregate each,
  "+" for a row count, a dimension dropped there counts its distinct values) and Filters (an
  operator by type, an enum or lookup field picks from its values, a date gets from → to; chips
  reorder by drag) — a **period bar** (the date field, the shared presets and calendar), Top N,
  and the result as a table: measures as numbers, dimensions through their enum / lookup rule,
  sortable by header. The analysis re-runs on its own when its definition changes. **Detail**
  view lists the rows with the Rows shelf as columns, paged; a click on an aggregate row
  **drills** to the rows behind it (the primary's keys, the dimensions, the measures' fields),
  with the cell's values shown and a way back. A superuser can show the SQL.
- **The page (second part): pivot, charts, KPI, calculated fields, the record.** The result
  switches between views without a new query. The **pivot** builds the row dimensions into a
  collapsible hierarchy (expand / collapse all) with one dimension spread as columns (the last
  one, or the one picked), subtotals on the group rows for an additive measure only (a sum of
  averages is meaningless, so a non-additive one keeps leaf values), a share of the parent or of
  the total beside each value, a grand total, Excel export, and a drill from any leaf cell.
  **Bars, lines and pie** go through the shared chart canvas: x is the first dimension, the series
  the second one (or the measures), the top 10 series or slices plus "Others", dates in
  chronological order and other categories by value, a stacked option; **KPI** shows one card per
  measure with its total. The table and the detail export to Excel too. The fields pane gains the
  **calculated fields**: "New calculated field" opens the editor (name, label, the expression
  with insert-a-field and insert-a-function pickers, a Test that compiles it on the server and
  shows the type and eight sample values; Save needs a passing test), the field then sits in a
  `calc` entity of the pane as a measure or a dimension, with edit and delete. In detail view and
  after a drill, a row of an entity that has a **screen** opens that screen's dialog: the screen's
  read query is run with the row's key values and the record shows as it does anywhere else,
  edits included.
- **Saved analyses and dashboards of analyses.** One `AnalysisDefinition` object drives the
  editor, the dashboards and (next) the digest. The page gains a mode bar — **Analyses** and
  **Dashboards** — with a picker of the saved ones (grouped by audience, "mine" flagged), Save,
  Save as and Delete. An analysis or a dashboard is stored on the saved-views store (`ly2_user_view`,
  kinds `analysis` / `explorer_dashboard`, the app as key, the id a slug of the name): **private**
  under its owner, or **shared** under the reserved `*` user so everyone with the app's explorer
  permission sees it; the owner, the audience, a version and the `bundled` marker ride in the
  record. Saving a shared object changes it for everyone (the modal says so, "Save as" makes
  your own copy); deleting a shared one is for its owner or a superuser. A **dashboard** is a grid
  of tiles (a saved analysis each, 1–4 columns wide, three heights), each running its analysis in
  its own view, with a period that overrides the analyses that have a period field; "Edit
  layout" moves, resizes, removes and adds tiles; the pencil opens the analysis in the editor, a
  drill from a tile too, with the cell's values as filters. A **bundled catalogue** ships with the
  app: `[explorer.<app>.analyses.<id>]` / `.dashboards.<id>` in `explorer.toml`, each with a
  `version`, installed once as shared objects at boot and on reload, refreshed on a version bump
  only while nobody edited the copy (a user save drops the marker), and never reinstalled once a
  user deleted it (an installed-ids marker row remembers). Routes `GET / PUT
  /api/explorer/<app>/saved/<analyses|dashboards>`, `DELETE …/<id>`.
- **Export and the headless run.** `POST /api/explorer/<app>/export` renders an analysis (saved,
  or the one on the page) or a dashboard (one section per tile) to **xlsx** (one sheet per
  section, bold headers and subtotals, the period and filters as a note) or **pdf** (through the
  reports pipeline, with the install's branding: a title, one chapter per section, GFM tables,
  bold group rows; needs the `reports` extra). A pivot exports flattened with its subtotals, a
  chart as its data table, a KPI as a row of totals, a detail as its rows; 3 000 rows at most per
  section. The buttons sit in the mode bar. Underneath, `liberty.explorer.report` holds the one
  server-side reading of a saved definition — the period presets resolved like the page does, an
  optional forced window (`from` / `to`) — and `build_sections`, which the C5 digest will call
  headlessly.
- **A model from a Screen Assistant preset.** The join graph is declared once, in the preset
  (`config/presets/**.toml`, shipped with the app like the model). In Settings → Explorer, picking
  a preset in the model's header **fills the model**: every preset table becomes an entity
  (`SELECT *` on the table, or the preset's fast-columns `query`; the id is the table name, the
  connector the preset's), the base table the hub, the joins relations on **data items**
  (`F0101.AN8 = F0111.AN8`) resolved on the discovered columns: the column of that name, the one
  whose dictionary item it is, or on a `dd_prefix` connector the one whose name minus the prefix
  is it (`ABAN8`, `WWAN8`). Entities already declared are kept and completed (the key, the
  measures, the default date, a cardinality stay yours); the result is what gets saved and the
  runtime reads only that. An entity may now be a `table` (+ `schema`) instead of a query, and the
  entity editor discovers a table's columns too. Routes `POST /admin/explorer/from-preset`,
  `POST /admin/explorer/describe-entity`; the Screen Assistant's catalogue reads the same files
  through the new `liberty.presets` loader.

**nomaflow: the digest — `report` and `notify` steps**
- Two new step types make a scheduled, e-mailed digest a plain job. **`report`** renders a saved
  Explorer analysis (`report = "analysis:<id>"`) or dashboard (`dashboard:<id>`, one section per
  tile) of an `app` / `model`, or a plugin report (`report:<scope>:<id>`, with `params`), to
  `format` xlsx or pdf, into the run's work area (the Run detail offers the file). The period is
  the step's `period` preset (`lastMonth`, `thisQuarter`…) or `window_from` / `window_to`, else
  the analysis's own. **Visibility**: `as_user = "cfo"` renders under that user's row-level scope
  (their roles' rules with their attributes — a "filiale" digest contains that company only);
  `as_role` under a role's literal rules; neither, unrestricted. **`notify`** sends the message
  through the notification dispatcher — `recipients` (`user:` / `role:` / `email:`), `subject`
  and `body` with `{JOB}` `{RUN_ID}` `{FILES}` `{DATE}`, `channels` — with the run's files
  **attached** (`attach = ["*"]`, or names). Both edit in the Job editor (the model, the shared
  analyses and dashboards, the reports, the roles are picked from lists); the schedule is the
  job's cron or presets as usual. The executors read the live app state, so a reload is seen.

**Screens: `editable_when`, a row-level read-only rule**
- A screen can say when an existing row may be changed: `editable_when = [{ field = "PERIOD_STATUS",
  value = ["open", "validated"] }]` keeps the rows of a closed period read-only. The grid's edit mode
  leaves such rows plain and shows a lock instead of the delete cross, the dialog opens them view-only,
  and the server refuses an update or delete whose original row fails the rule (409), including a
  request that leaves the rule's field out. New rows are not concerned. Rules AND together, values
  compare trimmed, like `disabled_when`. Set in the screen editor's General tab.

**Dashboards: a period**
- A dashboard can carry a `period`: a date range control in its bar, with the usual presets
  (today, yesterday, last 7 / 30 days, this / last month, this quarter, this / last year, last 12
  months, all dates), a two-month calendar for a free range, and, when the dashboard lists several
  date bases ("Document date", "Paid on"), the basis picker. The dashboard opens on
  `period.default`. Every widget query receives the range as the `:PERIOD_FROM` / `:PERIOD_TO`
  binds (bound as dates), plus `:PREV_FROM` / `:PREV_TO` for the period before (year to date compares with last year
  to date, a month with the previous month to date, a whole quarter with the previous quarter,
  anything else with the same length just before) and `:PERIOD_BASIS`; a query that does not
  mention them is left alone ("All dates" binds 0001-01-01 to 9999-12-31). When the period names the date column (`column`, or `dd` matched
  through the screens), a widget whose screen carries it is range-filtered server-side without a
  change to its SQL — the same wrap the filters use, now with `<COL>_from` / `<COL>_to` bounds on
  any hinted column. The dashboard editor has a Period block. The date range control and the
  floating panel are shared components for the lists and the explorer to come.
- App-level defaults in Settings → App: `default_period` (the preset a dashboard opens on when it
  names none; a pick lasts as long as the dashboard tab is open),
  `default_page_size` (the grid's initial page size; the SELECT row cap stays on the pool,
  connector and screen) and `dashboard_cache_seconds`. A reopened dashboard reuses each widget's
  result for that long; the bar's Refresh button bypasses it and says when the data was fetched.
- A KPI widget with `compare_previous = true` also runs its query on the period before and shows
  the change (in percent, or in points for a percent KPI, in green or red) and the previous value
  under the number. The widget editor has the checkbox.

**nomaflow: `file_load` step — a spreadsheet or CSV into a table, replayable by partition**
- What the grid's Excel import is not: a load that runs without a person at the keyboard (a
  schedule, a chain of steps) on the volumes a facts table takes, that can be replayed for one
  period without touching the others, and that leaves a trace. The step takes a `path` (with
  `${params.x}` from the run parameters and `${run.id}`), a `sheet`, the `header_row`, the CSV
  `delimiter` (detected when blank), a `target` connector / table, `columns` mapping target
  columns to file headers or `#positions` (the rest match by name), `constants` for the columns the
  file does not carry (`period = "${params.period}"`, `batch_id = "${run.id}"`), `load_mode =
  "append" | "replace_partition"` with `partition_by`, and `on_error = "fail" | "skip"`.
- The file is streamed, never loaded whole; cells are coerced to the target column's type
  (integers, decimals with either separator, dates in the usual shapes, booleans, text — a code the
  spreadsheet turned into a number comes back as text). `replace_partition` deletes the partitions
  the load carries (from the constants, else the distinct values found in the file) and inserts the
  rows in **one transaction**: a failed load leaves the previous data in place, a corrected month
  replaces that month only. The run log and the step result say rows read, inserted, rejected,
  partitions replaced and rows deleted, with the first rejects quoted. The step editor has its form.
- Three ways to name the file. The step's `path`; a file picked in the Run-with-parameters modal,
  uploaded to the job's work area and handed to the step for that fire (it wins over the path); or
  a `watch_dir` + `watch_pattern`, where every matching file is loaded in name order, each in its
  own transaction, then moved to `done/` or `failed/` under a time-stamped name — the scheduled
  "drop the file on the share" case. Rejected rows land in a CSV (file, row, reason) the run detail
  offers for download. `[jobs] files_dir` names the work area (default `<config dir>/.nomaflow/files`),
  `max_upload_mb` caps an upload.
- Two guards. `refuse_query`, a named query run before anything is touched, with the run
  parameters and the constants as binds: a row back refuses the load and its first column is the
  reason ("period 2026-02 is closed"). `control_column` + `control_total`: the column is summed
  over the loaded rows and compared, within `control_tolerance`, with a value, a parameter, or a
  cell of the file (`@Totals!B2`); a mismatch rolls that file's load back and names both numbers.

**Row-level data scope (first part)**
- A role can now restrict **which rows** its holders see, on top of which queries they may run:
  `[roles.<r>] scope = [{dd = "CO", values = ["00001"]}, …]` in `auth.toml` (the DB backend keeps
  the same list in `ly2_roles.scope`). A rule pins a dictionary item (`dd`) or one result column
  (`column`) to a set of values — literal, or taken from a user attribute (`source =
  "#USER.company#"`; users carry free-form `attributes`). Roles OR together, columns AND within a
  role; a role without scope grants everything; superusers bypass. `mode` decides what a query
  without the item does: `ignore` (unrestricted on that axis) or `deny` (no rows). `exclude = true`
  turns the list into a black-list (`SY NOT IN ('00')`, nulls kept). A rule may name
  its `connector`; elsewhere it says nothing. The scope is resolved at login and rides in the token
  — no database hit per request.
- The `dd` rule is the one that secures an item across tables. A result column carries the item
  when the screen's hint says so, when it is named like it, or — on a connector with the new
  `dd_prefix` (JDE: `dd_prefix = 2`, so `ABCO`, `MCCO`, `SDCO` all carry `CO`) — when its name
  minus the table prefix is the item. Every matching column of a query is restricted (a join of
  F0101 and F0006 binds both `ABCO` and `MCCO`), and no screen hint is needed per column. Text
  columns follow the filters' dialect rules: with the pool's `trim_strings` (JDE CHAR / NCHAR,
  blank-padded) the column is compared trimmed and the values stripped.
- Enforced in one place, the SQL connector: every SELECT (grid, stream, summary, export, dashboards,
  the AI assistant's queries, reports) is wrapped as `SELECT * FROM (<sql>) lib_rls WHERE …` with
  real binds, and an update or delete whose row lies outside the scope is refused (403). `/auth/me`
  reports the attributes and the resolved scope.
- **Roles are edited in a panel, on a menu tree.** Settings → Access → Roles shows the role next to
  the list instead of a modal full of permission strings. The role is the sidebar's own tree, one
  block per app, with inherit / allow / deny on every node: a folder cascades, a leaf shows what it
  runs, and an eye tells whether the sidebar will show it for a holder of the role, globs included.
  "Full access minus the Security folder" is the baseline plus one deny on the folder; "that folder
  only" is the empty baseline plus one allow. Rules menus don't cover (a whole connector, a query,
  an endpoint, a dashboard, the assistant, an expert pattern) sit below the tree. The strings
  written are the ones the previous editor wrote, so existing roles open as they are and nothing
  changes server-side.
- **Data scope in the role editor, attributes on the user.** The role panel has a Data scope tab:
  one line per rule ("Company (CO) in 00001, 00050", "SY not in 00", "CO = user.company"), with a
  form that picks the item from the dictionary, or a plain column name for a column that has no
  item (suggested from the screens of the connector), the values from the item's
  enum or lookup when it has one, the user attribute to read instead, exclusion, what a query
  without the item returns, and an optional connector. The user form gets a "Data-scope
  attributes" list (`company = 00001, 00050`, a comma makes a list; keys suggested from the roles'
  rules), and the profile shows the user's attributes and resolved scope in words.
- Setting it up without the UI: `liberty-admin create-role <r> --scope '{dd =
  "CO", source = "#USER.company#", mode = "deny"}'`, `set-scope <r> --rule … | --clear`,
  `list-roles`, `create-user --attribute company=00001,00050`, `set-attributes <u> KEY=VALUE…
  [--replace]` (`KEY=` removes); the same through `PUT /admin/access/roles/{name}` (`scope`) and
  the users' `attributes`. With OIDC, `[oidc] attribute_claims = { company = "company", pole =
  "department" }` fills the attributes from the ID token at every login (a mapped claim that
  disappears removes its attribute; attributes set locally are kept).

## 7.0.58 — 2026-10-27

**Install / upgrade**
- **Fixed: the image could not open its own default database.** `aiosqlite`, the driver behind the
  shipped `sqlite+aiosqlite://` default pool (and the Light layout), was a development-only
  dependency since the first release. The process still booted (the TOML auth backend needs no
  database), but change packages, saved views and nomaflow were silently disabled and `/ready`
  answered 503 on every Light install. The driver is now a runtime dependency.
- **Fixed: re-running `install.sh` (the documented way to switch TLS) or `docker compose -f
  docker-compose.full.yml pull` dropped the TLS and apps overlays** from the compose chain, so the
  container came back without `/apps` and seeded the empty templates — the install looked wiped.
  `install.sh` now reuses the `COMPOSE_FILE` recorded in `.env`; only `--ssl` swaps the TLS overlay.
- **New `release/liberty` command** — the one entry point for a Docker install: `install`, `start`,
  `stop`, `restart`, `status`, `doctor`, `ssl letsencrypt|provided|off`, `apps <wheel>`, `upgrade
  [--tag] [--apps]`, `backup`, `restore <dir>`, `logs`, `migrate`. The compose chain is state in
  `.env`, changed only by `ssl` and `apps`. `doctor` lists what is wrong and how to fix it;
  `upgrade` = doctor → backup gate → pull and recreate → schema migrations → status.
- **New `liberty-admin migrate [--dry-run]`** — applies (or lists) every app plugin's pending
  Alembic revisions (`plugins/<app>/db`), journaled under Settings → History → Upgrades. Replaces
  the manual run of the `nomasx1-upgrade-schema-1` job after an apps upgrade.
- `liberty-admin` now verifies the license key like the server does; before, every admin command
  silently dropped the licensed connectors.
- **`liberty upgrade` writes a report and `liberty rollback` undoes an upgrade.** Each run records
  what happened (versions before / after, image tag, compose chain, backup directory, doctor
  findings, every step's outcome, the schema migrations applied) as JSON + Markdown under the
  config volume (`.versions/upgrades/`) and journals it in Settings → History → Upgrades next to the
  version bumps (`liberty-admin record-upgrade`). Before pulling, the running image is kept under a
  local `rollback-<stamp>` tag; `liberty rollback` restores the pre-upgrade snapshot and re-runs
  that image.
- The container's healthcheck now probes `/ready` (the framework database answers) instead of
  `/info` (the process is up): a stack whose database is unreachable is reported `unhealthy`.
- The container warns in its log at boot when app schema migrations are pending;
  `LIBERTY_AUTO_MIGRATE=1` applies them at boot.
- **Fixed: a release that adds a column to one of the framework's own tables no longer breaks
  upgraded installs.** The nomaflow run history, change packages, saved views and the optional
  `ly2_*` auth tables were created with `create_all`, which never alters an existing table (change
  packages carried a hand-written `ALTER TABLE` list for exactly that). The stores now compare
  their models with the live tables at boot and add the missing columns, with the model's default
  so existing rows get a value; what was added is journaled as a `framework-schema` row in
  Settings → History → Upgrades, next to the apps' migrations.
- **Fixed: upgrading the licensed apps no longer loses operator edits — or misses product
  additions.** `liberty-apps install` copied whole files: without `--force-config` an existing file
  was kept as is (a new product screen, menu item or dictionary entry never reached the install);
  with it every file was replaced (your edits in screens, menus, dictionary, dashboards, charts and
  actions were lost — the `override` flag was ignored, only `connectors.toml` kept its secrets). The
  installer now merges **per entity**, with the rules Settings → Package & Deploy already applied:
  product additions are added; `override = true` entities, your own entities and entities you
  changed are kept; vendor entities you never touched are refreshed; what the vendor withdrew is
  removed only when untouched. `[pools.*]` and every `ENC:` secret always come from your file,
  `theme.toml` is yours. The shipment is recorded under `<config>/.bundled/` so the next upgrade
  can tell "untouched" from "edited". `--force-config` now means "also refresh vendor entities I
  changed without flagging them"; `--dry-run` prints the plan, and `liberty apps` / `liberty upgrade
  --apps` show it before writing. Entities kept because they changed locally are listed for review.
  (`liberty.config_merge`, shared with Package & Deploy.)
- **Hardening of the shipped layouts.** The container runs the app as the `liberty` user (uid
  1000) instead of root — it starts as root only to fix the ownership of the config volume and
  the apps mount of an older install, then drops privileges (`LIBERTY_RUN_AS_ROOT=1` opts out).
  Postgres is published on `127.0.0.1` only (`POSTGRES_BIND=0.0.0.0` opens it). The Traefik
  dashboard gets a random password per install (`liberty dashboard-password` regenerates it;
  `doctor` flags the historic default). The TLS overlays redirect HTTP to HTTPS and send
  security headers. Every `LIBERTY_*` / `POSTGRES_*` / `PGADMIN_*` variable also reads from
  `<NAME>_FILE` (Docker / Swarm / Kubernetes secrets).
- **Supply chain.** Python dependencies are pinned in `requirements.txt` (a pip-compile lock shared
  by the image, CI and the dev venv — the lock equals what the 7.0.57 image already ran); base
  images and the compose services are version-pinned and bumped by Dependabot; the release
  attaches an SBOM and provenance to the image and runs Trivy (fails on fixable CRITICAL / HIGH).
  The Oracle Instant Client is fetched from Oracle at image build time against a pinned SHA-256
  instead of 146 MB of zips in git (a local copy still works for air-gapped builds).
- **Request ids and JSON logs.** Every response carries `X-Request-ID` (the caller's or a fresh
  one) and every log line written while handling it carries the same id; `[app] log_format =
  "json"` emits one JSON object per line.
- **New `liberty-ops` command for Python installs** (pipx or venv, with or without systemd) — the
  twin of `release/liberty`: `status`, `doctor`, `start` / `stop` / `restart` / `logs`, `backup` /
  `restore` (config dir, apps dir, SQLite file, `pg_dump`), `migrate`, `upgrade [--version X]
  [--apps WHEEL]` (doctor → backup gate → `pipx upgrade` or pinned install → apps wheel →
  migrations → restart → report → status) and `rollback` (previous version + the pre-upgrade
  backup). Same report in Settings → History → Upgrades as the Docker path.

## 7.0.57 — 2026-10-26

**Reload**
- **Fixed: a config reload left some subsystems on the previous pool registry.** Change packages,
  saved grid views, the nomaflow job database and (on the file-watcher path) the auth backend kept
  the registry from before the reload and, because a disposed registry silently rebuilt engines
  from its old configuration, kept running with the previous pool credentials until a restart.
  Every reload path (Settings → Reload, the file watcher, a license rotation) now goes through one
  `rebuild_runtime` that re-points every component and closes the old registry for good.
- The framework now boots when the default database is down at startup (nomaflow is disabled for
  that run with a warning instead of blocking the whole app). New `GET /ready` readiness probe:
  `SELECT 1` on the default pool, 503 when it fails; `/health` stays a pure liveness check.

**Security**
- **DB errors no longer echo the SQL statement and the bound values** to regular users: they get the
  driver message plus an `error_id`; superusers keep the full text. The statement and binds are
  written to the server log only when the pool's `debug_sql` is on.
- **Refresh tokens rotate.** Each `/auth/refresh` returns a new refresh token and the one presented
  is consumed; presenting it again more than `[auth] refresh_reuse_grace` seconds later (default
  60 s — two browser tabs racing the same stored token) is refused. API clients must store the
  refresh token from every reply (the web app already does).
- **Login throttle**: after `[auth] login_max_failures` failed logins for a username inside
  `login_window_seconds` (default 10 in 5 min; 5× that per client address), `/auth/login` answers
  429 with `Retry-After`. `login_max_failures = 0` disables it.
- JWTs are signed with PyJWT (python-jose, unmaintained, is gone — same claims and secret, existing
  sessions keep working). A `LIBERTY_JWT_SECRET` shorter than 32 bytes is flagged once at startup.
- Anonymous `/info` reports counts and flags only; connector / pool names, the auth store location
  and the AI model show to authenticated callers.
- `POST /admin/import-package`: 50 MB upload cap and a 200 MB unpacked-size guard.

**Install**
- **Optional extras.** WeasyPrint (PDF reports), ldap3 (the `ldap_sync` job step) and Alembic +
  psycopg2 (the apps' schema migrations) are no longer core dependencies: install
  `liberty-next[all]` (or `[reports]` / `[ldap]` / `[alembic]`). The Docker image ships every extra
  unchanged. A core-only install boots without Pango; a PDF request then answers 503 with the
  install hint and markdown reports keep working.
- **Fixed: Package & Deploy upload never worked outside a venv that happened to carry
  `python-multipart`** — the dependency is now declared, and the upload check that rejected every
  real file as "missing 'package' file part" is corrected.

**Web app**
- **Faster first load**: the Monaco editor left the startup bundle (816 → 226 kB gzipped before the
  login page paints); it loads the first time a SQL editor opens.
- A rendering error in one tab no longer blanks the whole app: the tab shows the error with a
  Reload button and the other tabs keep working.
- The AI assistant reply can be **stopped** mid-stream; what was received is kept.
- The AI Assistant button follows the theme (a proper light-mode variant).

**Docs**
- `CLAUDE.md` is now a short map of the codebase; the full build narrative moved to
  `docs/history/`.

## 7.0.56 — 2026-10-26

**Connectors**
- **Fixed:** a hot reload of `connectors.toml` / `dictionary.toml` rebuilt the Oracle pools as *thin*
  engines (the file watcher didn't carry `[app] oracle_thick` into the rebuilt registry), so Native
  Network Encryption connections failed after any config edit until the next restart.

**Build, release and tests**
- **CI gates every release.** A new CI workflow runs the backend test suite and the frontend
  TypeScript check + production build on every push to `develop` and every pull request; the
  release workflow now waits for it, so nothing reaches PyPI or the Docker registry red.
- **Release notes are mandatory.** A release stops with a clear error when `RELEASE.md` has no
  section for the version being published (the previous behaviour inserted an empty placeholder —
  7.0.55 shipped that way). The git tag is now created *before* the PyPI publish.
- **Frontend lockfile repaired.** `package-lock.json` carried an inconsistent `yaml` entry that
  npm 10 rejected in the image build; it is regenerated and the image (and CI) now install with
  `npm ci`, which fails loudly if the lockfile ever drifts again.
- The health-endpoint tests no longer depend on the developer machine's local configuration.
- Dependabot keeps Python, npm, GitHub Actions and Docker base-image dependencies current
  (weekly, grouped, against `develop`).

**Docs**
- Removed references to planning documents that no longer exist (`docs/PLAN.md`,
  `docs/PHASE13.md`) and to a `liberty-apps/Dockerfile` that never did; the nomaflow operating
  guide lives in the apps repo (`docs/NOMAFLOW.md`).

## 7.0.55 — 2026-07-16

**Connectors — Oracle thick mode (Native Network Encryption)**
- **App-wide `[app] oracle_thick`.** A customer enabled Oracle Native Network Encryption, which
  python-oracledb's thin (pure-Python) mode can't negotiate (`DPY-3001`). When the flag is on, every
  Oracle pool is built on the **thick** OCI client (the Instant Client bundled in the image) instead
  of the thin async driver — `ThickAsyncEngine` presents the same `AsyncEngine` surface the framework
  uses (execute / stream / begin / dispose) over a sync SQLAlchemy engine, running each connection's
  DB calls on its own single-thread executor (oracledb connections aren't thread-safe). Postgres
  pools are untouched.
- The flag is **process-wide, not per pool**: `init_oracle_client()` is global to the process, so
  thick vs thin is all-or-nothing. It's exposed in Settings → App; a restart applies it.

## 7.0.54 — 2026-07-14

**Connectors — multi-environment (one connector, many pools)**
- **A SQL connector can now run against several pools.** Declare `pools = ["…"]` alongside the
  default `pool`, and the *same* queries + screens run against any of them — one connector serving
  multiple DB / JDE instances, no cloning of connectors or duplicating screens. The pool is chosen
  at runtime:
  - **UI** — the app switcher shows a **Pool** picker when the current app spans a multi-pool
    connector; picking a pool re-runs its screens against that instance. The choice rides on the
    `X-Liberty-Pool` header and is applied only to connectors that actually have that pool (an app's
    screens hit several connectors — a single-pool one keeps its default rather than erroring). The
    pick is per app and persisted.
  - **Jobs** — `sql_copy` / `sql_query` steps take `source_pool` / `target_pool` to target a
    specific instance without new connectors.
- The connector's `pool` (default) and `pools` (extra) are edited from a **dropdown of configured
  pools** in Settings → Connectors → Settings…, instead of typing names.
- Fully back-compatible: a connector with only `pool` behaves exactly as before. The allowed set is
  `[pool] + pools`; a request/job can't reach a pool outside it. Pool *names* are exposed to the UI
  picker — never the pool URL or credentials.

## 7.0.53 — 2026-07-14

**nomaflow**
- **Schedulable presets.** A job preset (a saved set of run parameters) can now carry its own
  cron + timezone. The scheduler fires the job on that schedule with the preset's params /
  op_kwargs / step toggles / log level — so one job runs on several schedules with different
  parameters, without cloning. Each preset has its own **enable** toggle, **independent of the
  job's `enabled`**: an enabled preset fires even when the job's own schedule is off (the job's
  `enabled` gates only the job's own cron). The Schedule view now lists one row per schedule (the
  job's cron plus each preset's), and a scheduled-but-disabled trigger is marked so it's clear
  why it isn't firing.
- **Name a clone; delete a job.** Duplicating a job now prompts for the new id (the editor's id is
  immutable, so the old auto `<id>_copy` couldn't be renamed). A **Delete** action removes a job
  from `jobs.toml` (config only — past run history is kept).
- **Fixed** the Run-with-parameters modal overflowing horizontally and pushing the "Save as
  preset" button off-screen.

**Docker**
- **pgAdmin binds to IPv4.** Recent `dpage/pgadmin4` images default the gunicorn listen address to
  `[::]` (IPv6); on a host with IPv6 disabled that fails to bind (`Address family not supported by
  protocol`) and pgAdmin never starts. The full-stack compose now sets
  `PGADMIN_LISTEN_ADDRESS=0.0.0.0`.

## 7.0.52 — 2026-07-05

**Docker**
- **Oracle thick-mode client now loads on the current base image.** The bundled Instant Client
  needs `libaio.so.1`, but Debian trixie ships libaio as `libaio1t64` providing `libaio.so.1t64`
  (the time_t package rename) — so `init_oracle_client()` failed with `DPI-1047: libaio.so.1:
  cannot open shared object file` even though the client and libaio were both present. The image
  now installs libaio in its own layer (no `--no-install-recommends`, which had skipped the real
  package) and adds a `libaio.so.1 → libaio.so.1t64` compat symlink — ABI-identical on the 64-bit
  arches we build, so thick-mode LOB-over-dblink fetches work out of the box with no manual step.
- **Release notes now bundled in the image.** `.dockerignore` excluded `**/*.md`, which stripped
  `RELEASE.md` / `RELEASE.fr.md` from the build context before they could be packaged — so
  Settings → Release notes showed "No release notes bundled with this build" in the container
  (it worked from source on a dev box). The two release-note files are now re-included.

## 7.0.51 — 2026-07-05

**Connectors**
- **Thick-mode fetch for LOBs over a database link.** python-oracledb's async (thin) mode can't
  fetch a LOB across a DB link (fails with ORA-22992 / ORA-03149 — the LOB comes back as a remote
  locator). `liberty.connectors.thick.fetch_thick(pools, pool, sql)` runs such a query in a
  short-lived subprocess that turns on the thick (OCI) client *there and only there*, returning the
  rows (LOBs materialised as bytes) to the async caller — the main app stays thin/async. The Oracle
  **Instant Client is now bundled in the image** (amd64 + arm64), and `PoolRegistry.oracle_connect_params`
  exposes a pool's decrypted DSN/creds for it. Use only where thin genuinely can't (a LOB SELECT over
  a dblink); everything else stays on the async driver.

## 7.0.50 — 2026-07-04

**Connectors**
- **Per-schema DB links (`#DBLINK.<NAME>#`)** — a pool can now map a DB-link suffix per schema
  (`[pools.X] dblinks = { SY = "@ORCLPROD", … }`), appended to a table *after* its
  `#SCHEMA.<NAME>#` prefix (`…F0092#DBLINK.SY#` → `SY920.F0092@ORCLPROD`). An unmapped or empty
  token drops out, so the same query runs locally where the schema isn't remote; a non-empty value
  must be a bare `@link` reference (config-injection guard).

**nomaflow**
- **Plugin log lines now reach the UI run log.** The per-run `RunLogHandler` was only bound to the
  `liberty.*` logger tree, so a plugin namespace registered before startup (e.g. `nomasx1`) had its
  records appear on stdout but never in the UI run log. `install()` now binds *every* registered
  namespace, honouring the deferred-attach contract.
- **Configurable run-log buffer** — `[jobs] run_log_max_lines` (default 5000) caps the per-run log
  ring buffer; raise it for jobs that legitimately emit tens of thousands of lines (e.g. a per-table
  ETL over thousands of tables) so the run's head isn't dropped.

## 7.0.49 — 2026-06-21

**Docs**
- French release notes (`RELEASE.fr.md`) brought up to date through 7.0.48 — native French entries
  for 7.0.39–7.0.48 (the file had been frozen at 7.0.27).

## 7.0.48 — 2026-06-21

**Screens**
- **`default_when` lock flag** — a conditional default can now seed a value without locking the
  field. `lock = false` fills the value when the condition becomes active but leaves it editable
  (e.g. default a lookup column to `*ALL` while the operator can still narrow it); `lock` omitted /
  `true` keeps the previous force-and-disable behaviour.

**Settings**
- Release notes render at the panel's text scale (the changelog no longer shows oversized headings).

## 7.0.47 — 2026-06-15

**Dialog**
- Hidden / non-field **key columns now rebind correctly** on save — the edit dialog snapshots every
  loaded column for the `_put`'s `:<COL>_ORIGINAL` binds (not just the rendered fields), so a row
  whose key is a hidden column updates the right record.
- **Dirty-detection** is scoped to the editable fields again, so cancelling a dialog without changes
  no longer pops the "unsaved changes" prompt.

**Wizards**
- The CRUD generator emits **upper-cased** column identifiers and bind names (matching the SELECT
  wizard) — JDE columns come out uppercase.
- The lookup / sequence generator no longer requires a table: you can **write the SQL directly** and
  still "Use this query".

**SQL / errors**
- Database errors split the **SQL and bound parameters into readable blocks** (one bind per line) in
  the expandable error detail.
- Colons inside string literals are escaped before SQLAlchemy parses binds, so a literal like
  `':0'` is no longer mistaken for a bind parameter.

## 7.0.46 — 2026-06-15

**Auth**
- **Silent token refresh** — the access token is refreshed automatically (a proactive timer plus a
  refresh-and-retry on any 401), so an active session no longer drops roughly every hour.

**Errors**
- Raw database errors collapse to a **concise summary with an expandable detail** in the screen
  dialog and the grid bulk-edit / proxy banners, instead of dumping the whole driver message.

**Dialog**
- Disabled **BOOLEAN / ENUM** fields render as a read-only checkbox / label (matching the grid),
  not the raw stored code.

## 7.0.45 — 2026-06-14

**Screen Creation Assistant**
- Build a screen from an **existing connector query** (read-only reuse), not only a physical table;
  query-backed catalog presets reuse the query instead of generating a duplicate.
- The **Grid view** step (the default shared view) is separated from the optional **Dialog columns**
  step; the read query selects every column and the view/dialog choose what's shown.
- Created screens set **`auto_load`** and the workspace refreshes after scaffolding, so the new
  screen opens without a manual browser reload.
- Column pickers: **tree-style parent-menu** picker, **collapsible + searchable** source-table
  groups, full-height scroll panes, per-table "add all", and loading spinners.

## 7.0.44 — 2026-06-13

**Audit trail**
- **In-flight value diff** — a row expands to its field-level BEFORE / AFTER values parsed on demand
  from the stored DML statement, so the source values table can be purged and still reconstructed.
- **Purge** and **rebuild-values** jobs to manage the audit history.
- Summary-view UX refinements (native expandable sub-rows in place of a nested grid).

## 7.0.43 — 2026-06-13

**Grid**
- **Saved grid views** — named shared views plus per-user views (columns, sort, grouping, page
  size), available from the grid's view picker.
- **Summary view** — server-aggregated parent rows (accurate counts over the full set) with a
  chevron that lazy-loads the underlying rows; per-day / month / year bucketing.

**Filters & jobs**
- Day-grain filtering on timestamp columns; a **WARNING** job run level.

## 7.0.42 — 2026-06-13

**Security**
- **PASSWORD-ruled columns** are encrypted at rest (AES-GCM `ENC:`), and a blank password on an
  UPDATE preserves the stored secret instead of overwriting it.

## 7.0.41 — 2026-06-13

**Reports**
- **Run-form dropdowns** — report parameters can declare `options`, and the run dialog renders a
  searchable select instead of a free input. Choices resolve server-side (a static list, the
  configured connectors, a connector's schemas, or a named connector query mapped to value/label),
  and cascade — e.g. an Application picker that lists apps by name on the chosen connector.

## 7.0.40 — 2026-06-12

**Docs**
- README: upgrade history & release notes, `release/upgrade.sh`, and a failed-publish recovery note
  (incl. the GitHub-App workflow-file tag-push limitation).

## 7.0.39 — 2026-06-12

**Config history**
- **Upgrade history** tracks software version changes on startup — framework **and** licensed apps,
  independently — surfaced under Settings → History → Upgrades with the release notes inline.
- **Release notes** shipped in the wheel (`RELEASE.md` / `.fr`), served per component.

## 7.0.27 — 2026-06-12

**Screen Creation Assistant**
- New superuser **Screen Creation Assistant** (sidebar, above Monitoring) — a guided wizard that turns
  picked tables into a live screen + dialog + menu in one pass: tables & joins, columns → tabs,
  dictionary review, and menu placement.
- **Catalog presets** — operator-managed table presets (`config/presets/`), browsable and searchable;
  selecting one pre-wires the joins. Ships a JD Edwards Address Book catalog.

**Dictionary**
- Shared scan table between the Dictionary editor and the assistant: existing entries are detected and
  only missing ones are proposed, with Format/Rule dropdowns, rule value, **UDC lookup params**, and
  default.

**Config history**
- **Upgrade history** — Settings → History → Upgrades records each application version change.
- **Release notes** — this view.

## 7.0.26 — 2026-06-01

- Screen + dependency **bundle versioning** (Phase 2): every screen save snapshots its dependency
  closure; restore reverts the screen *with* its queries/lookups/dictionary entries.
- **Retention purge** job for config-version snapshots; per-version and per-file delete.
- Read-only screen mode; move a field between dialog tabs from the inspector.
