# Liberty Next — Notes de version

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
