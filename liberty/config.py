from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_APP_CONFIG = Path("config/app.toml")


class AppSettings(BaseModel):
    name: str = "Liberty v2"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


class ConnectorSettings(BaseModel):
    config_path: Path = Path("config/connectors.toml")


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    connectors: ConnectorSettings = Field(default_factory=ConnectorSettings)


def load_settings(path: Path | str = DEFAULT_APP_CONFIG) -> Settings:
    path = Path(path)
    if not path.exists():
        return Settings()
    with path.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return Settings.model_validate(data)
