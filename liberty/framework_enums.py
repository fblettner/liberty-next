"""Framework-level reusable enums for the builder UI — v2's port of v1's ``ly_enum`` rows that
powered the framework's *own* configurator (the "Dictionary Type", "Datasource Type", "HTTP Method"
… dropdowns shown when editing pools/connectors/dictionary/etc.).

The schema-driven builder UI doesn't need to know what values a `format` or `dialect` field can take —
the config models annotate the field with ``json_schema_extra={"x_enum_ref": "DICTIONARY_TYPE"}``
and the frontend looks the values up in this registry. Each entry has a human ``label`` (for the
panel title), and ``values`` with ``value`` (the code stored in the config) + ``label`` (the
description column). This is the v2 form of v1's ``ly_enum`` / ``ly_enum_val`` rows for
``enum_id < 1000`` (the framework's own).

Bundled with the framework (this Python module — read-only from the UI). A future Phase-7 slice will
let operators *override* / *extend* these via ``config/dictionary.toml`` (under a reserved scope, so
adding a new "Datasource Type" doesn't require a code change). The app-data dictionary enums users
define for their own queries (``[enums.STATUS]`` etc. — Active/Inactive in their data) live in
``dictionary.toml`` and aren't surfaced here.
"""

from __future__ import annotations

from typing import Any

FRAMEWORK_ENUMS: dict[str, dict[str, Any]] = {
    "DICTIONARY_TYPE": {
        "label": "Dictionary Type",
        "values": [
            {"value": "boolean", "label": "Boolean"},
            {"value": "date", "label": "Date"},
            {"value": "datetime", "label": "Date & Time"},
            {"value": "timestamp", "label": "Timestamp"},
            {"value": "jdedate", "label": "JD Edwards Date"},
            {"value": "number", "label": "Number"},
            {"value": "integer", "label": "Integer"},
            {"value": "decimal", "label": "Decimal"},
            {"value": "currency", "label": "Currency"},
            {"value": "text", "label": "Text"},
            {"value": "textarea", "label": "Long Text"},
            {"value": "password", "label": "Password"},
            {"value": "email", "label": "Email"},
            {"value": "url", "label": "URL"},
            {"value": "color", "label": "Color"},
        ],
    },
    "DICTIONARY_RULES": {
        "label": "Dictionary Rules",
        "values": [
            {"value": "BOOLEAN", "label": "Boolean (Y/N)"},
            {"value": "COLOR", "label": "Color Picker"},
            {"value": "CURRENT_DATE", "label": "Date Today"},
            {"value": "DEFAULT", "label": "Default value"},
            {"value": "DISABLED", "label": "Disable Dictionary Rule"},
            {"value": "ENUM", "label": "Enumeration"},
            {"value": "JDEDATE", "label": "JD Edwards Date"},
            {"value": "LOGIN", "label": "User connected"},
            {"value": "LOOKUP", "label": "Lookup Table"},
            {"value": "NN", "label": "Next Number"},
            {"value": "PASSWORD", "label": "Password (mask and encryption)"},
            {"value": "SEQUENCE", "label": "Sequence"},
            {"value": "SYSDATE", "label": "Date Today"},
        ],
    },
    # QUERY_TYPE retired — a query's role is implied by which section it sits under in
    # connectors.toml (``[[connectors.X.tables]]`` / ``queries`` / ``sequences`` /
    # ``lookups``). Kept here as a comment so a future contributor searching for it lands
    # on this explanation instead of resurrecting the enum.
    "DATASOURCE_TYPE": {
        "label": "Datasource Type",
        "values": [
            {"value": "postgresql", "label": "Postgres"},
            {"value": "oracle", "label": "Oracle"},
            {"value": "mysql", "label": "MySQL"},
            {"value": "mariadb", "label": "MariaDB"},
            {"value": "mssql", "label": "Microsoft SQL Server"},
            {"value": "sqlite", "label": "SQLite"},
            {"value": "db2", "label": "IBM DB2"},
        ],
    },
    "HTTP_METHOD": {
        "label": "HTTP Method",
        "values": [
            {"value": "GET", "label": "GET"},
            {"value": "POST", "label": "POST"},
            {"value": "PUT", "label": "PUT"},
            {"value": "PATCH", "label": "PATCH"},
            {"value": "DELETE", "label": "DELETE"},
            {"value": "HEAD", "label": "HEAD"},
            {"value": "OPTIONS", "label": "OPTIONS"},
        ],
    },
    "COLUMN_ALIGN": {
        "label": "Column Alignment",
        "values": [
            {"value": "left", "label": "Left"},
            {"value": "right", "label": "Right"},
            {"value": "center", "label": "Center"},
        ],
    },
    "AUTH_TYPE": {
        "label": "Authentication Type",
        "values": [
            {"value": "none", "label": "None"},
            {"value": "basic", "label": "Basic"},
            {"value": "bearer", "label": "Bearer Token"},
            {"value": "api_key", "label": "API Key"},
            {"value": "oauth2", "label": "OAuth 2.0"},
        ],
    },
    "QUERY_DEFINITION": {
        "label": "Query Definition (CRUD)",
        "values": [
            {"value": "get", "label": "Select"},
            {"value": "put", "label": "Update"},
            {"value": "post", "label": "Insert"},
            {"value": "delete", "label": "Delete"},
        ],
    },
    "MENU_ITEM_TYPE": {
        "label": "Menu Item Type",
        "values": [
            {"value": "screen", "label": "Screen (designed view)"},
            {"value": "query", "label": "Query (raw table view)"},
            {"value": "endpoint", "label": "Endpoint (API call)"},
            {"value": "dashboard", "label": "Dashboard (widget grid)"},
            {"value": "page", "label": "Page (frontend route)"},
        ],
    },
    "SUPPORTED_LANGUAGES": {
        "label": "Languages",
        "values": [
            {"value": "en", "label": "English"},
            {"value": "fr", "label": "Français"},
            {"value": "de", "label": "Deutsch"},
            {"value": "es", "label": "Español"},
            {"value": "it", "label": "Italiano"},
            {"value": "pt", "label": "Português"},
            {"value": "nl", "label": "Nederlands"},
            {"value": "pl", "label": "Polski"},
            {"value": "ja", "label": "日本語"},
            {"value": "zh", "label": "中文"},
            {"value": "ar", "label": "العربية"},
        ],
    },
    "BOOLEAN_TRUE_VALUES": {
        "label": "Boolean True Values",
        "values": [
            {"value": "Y", "label": "Y / N (default)"},
            {"value": "1", "label": "1 / 0"},
            {"value": "01", "label": "01 / 02"},
            {"value": "true", "label": "true / false"},
            {"value": "T", "label": "T / F"},
        ],
    },
}
