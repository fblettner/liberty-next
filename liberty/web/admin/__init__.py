# isort: skip_file
# ruff: noqa: F401 — a re-export hub: every name of the former single-file module stays importable from here
"""``/admin`` routes — operational endpoints (superuser only).

* ``POST /admin/reload`` rebuilds the :class:`ConnectorRegistry` from
  ``connectors.toml`` on disk (and re-reads ``menus.toml``), swaps them into
  ``app.state`` (also rebuilding the auth backend — for the DB backend, against the
  new pool registry), then disposes the old registry. New connector definitions, edited queries and
  menu changes take effect immediately for subsequent requests. The AI assistant's
  connector tools still reference the previous registry until the app restarts;
  in-flight requests keep using whichever registry they started with.
* ``GET /admin/config/connectors`` returns the raw ``connectors.toml`` text.
* ``PUT /admin/config/connectors`` validates the submitted TOML (parsed against
  the connector schema) and, only if it's valid, writes it back to disk. It does
  *not* reload — call ``POST /admin/reload`` afterwards to apply.
* ``GET /admin/config/schema`` returns the JSON Schema of the structured-config
  models (currently ``pool``) — the config-builder UI renders its forms from it.
* ``GET/PUT /admin/config/pools`` — the structured ``[pools.*]`` view: GET returns
  ``{name: PoolConfig dict}``; PUT validates each against ``PoolConfig`` and surgically
  rewrites only the ``[pools.*]`` tables in ``connectors.toml`` (comments/formatting of the
  rest preserved, via ``tomlkit``). PUT does *not* reload — call ``POST /admin/reload`` after.
  (First slice of the Phase-7 config builders — the same shape will grow to connectors, queries, …)
"""

from __future__ import annotations

# The names the single-file module imported, still reachable as ``liberty.web.admin.<name>``.
import copy
import logging
import tomllib
from pathlib import Path
from typing import Annotated, Any
import tomlkit
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from liberty.auth.dependencies import require_superuser
from liberty.auth.principal import Principal
from liberty.config import substitute_env
from liberty.crypto import CryptoError, encrypt, is_encrypted
from liberty.connectors.config import (
    ApiConnectorConfig,
    ConnectorsFile,
    PoolConfig,
    SqlConnectorConfig,
    load_connectors_file,
    parse_connectors,
)
from liberty.connectors.dictionary import (
    DictionaryFile,
    load_dictionary,
    parse_dictionary,
)
from liberty.framework_enums import FRAMEWORK_ENUMS
from liberty.licensing import ALWAYS_LICENSED_CONNECTORS, verify_license
from liberty.menus import load_menus
from liberty.menus.config import MenusFile, parse_menus
from liberty.screens import Screen, load_screens
from liberty.screens.config import ScreensFile, parse_screens
from liberty.actions import load_actions
from liberty.actions.config import SharedActionsFile, parse_actions
from liberty.charts import load_charts
from liberty.charts.config import ChartsFile, parse_charts
from liberty.dashboards import load_dashboards
from liberty.explorer import ExplorerFile
from liberty.dashboards.config import DashboardsFile, parse_dashboards
from liberty.theme import font_choices, load_theme, parse_theme, preset_choices, resolve_theme
from liberty.web.clone import CloneError, clone_app, delete_app
from liberty.web.move import MoveError, move_query
from liberty.web.rename import (
    RenameError,
    rename_connector,
    rename_dictionary_entry,
    rename_lookup,
    rename_action,
    rename_query,
    rename_screen,
    rename_screen_app,
    rename_sequence,
    rename_table,
)
from liberty.web.jobs import Editor as JobsEditor
from liberty.ai.appconfig import AiFile, parse_ai_config
from liberty.ai.catalog import KNOWN_MODELS
from liberty.config import AISettings, AppSettings, AuthSettings, ChangeSetsSettings, JobsSettings, LicenseSettings, OIDCSettings, ReportsBrandingSettings, load_settings, resolve_app_config_path, NotifySettings
from liberty.web.usages import find_usages
from liberty.web.dependencies import Seed, collect_dependencies
from liberty.web.package import build_package_zip
from liberty.web.clone_with_deps import Seed as CloneSeed, clone_with_deps
from liberty.web.delete_with_deps import Seed as DeleteSeed, plan_delete_with_deps

# The section modules, imported IN THIS ORDER — the order their routes were registered in the single
# file: FastAPI matches routes in registration order (keep it; isort is off for this file).
from liberty.web.admin._base import (
    Superuser,
    _drop_none,
    _dump_screen,
    _log,
    _reinject_action_type,
    _snapshot_all_config,
    _snapshot_config,
    router,
)
from liberty.web.admin.bundles import (
    _BUNDLE_PREFIX,
    _capture_screen_bundles,
    _screen_bundle_key,
    reload_connectors,
)
from liberty.web.admin.versions import (
    clear_config_versions,
    config_version_content,
    config_version_download,
    delete_config_version,
    list_config_versions,
    read_config_raw,
    restore_config_version,
)
from liberty.web.admin.screen_versions import (
    clear_screen_versions,
    delete_screen_version,
    list_screen_versions,
    restore_screen_version,
    screen_version_contents,
    screen_version_download,
)
from liberty.web.admin.upgrades import (
    get_release_notes,
    get_upgrade_report,
    list_upgrades,
)
from liberty.web.admin.sql_test import (
    TestSqlBody,
    test_sql,
)
from liberty.web.admin.builders import (
    ConnectorsParsedBody,
    DictionaryBody,
    MenusBody,
    PoolsBody,
    ScreensBody,
    _API_CONNECTOR_PASSWORD_FIELDS,
    _POOL_PASSWORD_FIELDS,
    _dictionary_path,
    _encrypt_secret_fields,
    _merge_screens,
    _order_like,
    config_integrity,
    get_config_schema,
    get_connectors_parsed,
    get_dictionary_parsed,
    get_menus_parsed,
    get_pools_config,
    get_screens_parsed,
    put_connectors_parsed,
    put_dictionary_parsed,
    put_menus_parsed,
    put_pools_config,
    put_screens_parsed,
)
from liberty.web.admin.assistant import (
    ScaffoldBody,
    ScaffoldMenuItem,
    ScaffoldTable,
    assistant_scaffold,
    list_assistant_presets,
)
from liberty.web.admin.modules import (
    ActionsBody,
    AiBody,
    ChartsBody,
    DashboardsBody,
    DescribeEntityBody,
    ExplorerBody,
    FromPresetBody,
    NotificationsBody,
    _explorer_model_dump,
    _module_or_404,
    _module_plan,
    _modules_dir,
    apply_module,
    explorer_describe_entity,
    explorer_from_preset,
    get_actions_parsed,
    get_ai_parsed,
    get_charts_parsed,
    get_dashboards_parsed,
    get_explorer_parsed,
    get_notifications_parsed,
    list_modules,
    preview_module,
    put_actions_parsed,
    put_ai_parsed,
    put_charts_parsed,
    put_dashboards_parsed,
    put_explorer_parsed,
    put_notifications_parsed,
)
from liberty.web.admin.theme import (
    ThemeBody,
    get_theme_parsed,
    put_theme_parsed,
)
from liberty.web.admin.jobs import (
    JobsBody,
    _merge_jobs,
    _restore_env_refs,
    get_jobs_parsed,
    put_jobs_parsed,
)
from liberty.web.admin.app_settings import (
    AppConfigBody,
    ReportsBrandingBody,
    ReportsTemplateColumnsBody,
    ReportsTemplatePreviewBody,
    ReportsTemplatesBody,
    _AI_MODELS,
    _AI_SAFE_KEYS,
    _APP_SAFE_KEYS,
    _APP_TOML_SENSITIVE_FIELDS,
    _AUTH_SAFE_KEYS,
    _CHANGESETS_SAFE_KEYS,
    _JOBS_SAFE_KEYS,
    _LICENSE_SAFE_KEYS,
    _NOTIFY_SAFE_KEYS,
    _OIDC_SAFE_KEYS,
    _PREVIEW_ROW_CAP,
    _app_config_path,
    _encrypt_sensitives,
    _pick,
    _readonly_settings,
    get_app_parsed,
    get_reports_branding,
    get_reports_parsed,
    introspect_report_query_columns,
    preview_report_template,
    put_app_parsed,
    put_reports_branding,
    put_reports_parsed,
)
from liberty.web.admin.usages import (
    BuildPackageBody,
    ExportJobBody,
    FindDependenciesBody,
    FindUsagesBody,
    IncludeItem,
    MAX_PACKAGE_BYTES,
    SeedItem,
    build_package_endpoint,
    export_job_endpoint,
    find_dependencies_endpoint,
    find_usages_endpoint,
    import_package_endpoint,
)
from liberty.web.admin.proposals import (
    ProposalApplyBody,
    _APPLY_DISPATCH,
    _apply_connector_queries,
    _apply_dictionary_entries,
    _apply_menu_item,
    _apply_screen,
    apply_proposal,
)
from liberty.web.admin.clone import (
    CloneWithDepsBody,
    _apply_chart_clone,
    _apply_dashboard_clone,
    clone_with_deps_endpoint,
)
from liberty.web.admin.delete import (
    DeleteWithDepsBody,
    _delete_chart,
    _delete_connector_queries,
    _delete_dashboard,
    _delete_screen,
    delete_with_deps_endpoint,
    delete_with_deps_preview,
)
from liberty.web.admin.api_test import (
    ApiTestBody,
    test_api_connector,
)
from liberty.web.admin.rename import (
    MoveBody,
    RenameBody,
    move_query_between_connectors,
    rename_top_level_key,
)
from liberty.web.admin.clone_app import (
    CloneAppBody,
    DeleteAppBody,
    clone_app_endpoint,
    delete_app_endpoint,
)
