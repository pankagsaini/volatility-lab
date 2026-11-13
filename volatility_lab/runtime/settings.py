"""Validated process configuration. No I/O except an explicitly named env file."""

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import re

from volatility_lab import PACKAGE_ROOT


class ConfigurationError(ValueError):
    """Safe-to-display errors name a field, never its secret value."""


@dataclass(frozen=True)
class Settings:
    environment: str
    data_dir: Path
    archive_dir: Path | None
    cloud_url: str
    offline: bool
    log_level: str
    port: int
    upstox_token: str | None = field(default=None, repr=False)


def _env_file(path):
    result = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ConfigurationError("VOL_LAB_ENV_FILE could not be read") from None
    for index, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)", line)
        if not match:
            raise ConfigurationError(f"VOL_LAB_ENV_FILE has invalid syntax on line {index}")
        key, value = match.groups()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigurationError(f"VOL_LAB_ENV_FILE has an unclosed quote on line {index}")
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        result[key] = value  # Deliberately no shell expansion or interpolation.
    return result


def _absolute(value, name):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(name + " must be an absolute path")
    return path.resolve()


def load_settings(environ=None):
    env = dict(os.environ if environ is None else environ)
    if env.get("VOL_LAB_ENV_FILE"):
        env = {**_env_file(env["VOL_LAB_ENV_FILE"]), **env}
    profile = env.get("VOL_LAB_ENV", "local").strip().lower() or "local"
    if profile not in ("local", "test", "staging", "production"):
        raise ConfigurationError("VOL_LAB_ENV must be local, test, staging or production")
    level = env.get("VOL_LAB_LOG_LEVEL", "INFO").upper() or "INFO"
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigurationError("VOL_LAB_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")
    raw_offline = env.get("VOL_LAB_OFFLINE", "false").strip().lower() or "false"
    if raw_offline not in ("0", "1", "false", "true"):
        raise ConfigurationError("VOL_LAB_OFFLINE must be true or false")
    try:
        port = int(env.get("STREAMLIT_SERVER_PORT") or 8501)
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise ConfigurationError("STREAMLIT_SERVER_PORT must be between 1 and 65535") from None
    # Keep existing workstations on their original history until an operator selects another directory.
    legacy = PACKAGE_ROOT / "data"
    existing = any(
        (legacy / name).exists()
        for name in (
            "monitor.sqlite",
            "futures/futures.sqlite",
            "google_archive/manifest.json",
            "upstox/research.sqlite",
            "backtest_1minute/downloads.sqlite",
        )
    )
    state_home = Path(env.get("LOCALAPPDATA") or env.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    default_data = legacy if existing and profile == "local" else state_home / "volatility-lab" / profile / "data"
    data = _absolute(env["VOL_LAB_DATA_DIR"], "VOL_LAB_DATA_DIR") if env.get("VOL_LAB_DATA_DIR") else default_data
    archive = _absolute(env["VOL_LAB_ARCHIVE_DIR"], "VOL_LAB_ARCHIVE_DIR") if env.get("VOL_LAB_ARCHIVE_DIR") else None
    try:
        saved = json.loads((PACKAGE_ROOT / "cloud/apps_script/sync_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    url = (env.get("GOOGLE_APPS_SCRIPT_WEBHOOK_URL") or saved.get("web_app_url") or "").strip()
    if url:
        try:
            parsed = urlsplit(url)
            port_value = parsed.port
        except ValueError:
            raise ConfigurationError("Invalid GOOGLE_APPS_SCRIPT_WEBHOOK_URL") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != "script.google.com"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or port_value not in (None, 443)
            or not re.fullmatch(r"/macros/s/[A-Za-z0-9_-]+/exec", parsed.path)
        ):
            raise ConfigurationError(
                "GOOGLE_APPS_SCRIPT_WEBHOOK_URL must be a public HTTPS Apps Script /exec URL without credentials or query parameters"
            )
    token = (env.get("UPSTOX_TOKEN") or env.get("UPSTOX_ACCESS_TOKEN") or "").strip() or None
    if token and ("\n" in token or "\r" in token):
        raise ConfigurationError("UPSTOX_TOKEN must be a single line")
    return Settings(profile, data, archive, url, raw_offline in ("1", "true"), level, port, token)


def data_dir():
    return load_settings().data_dir


def data_for_root(root):
    return data_dir() if Path(root).resolve() == PACKAGE_ROOT else Path(root) / "data"
