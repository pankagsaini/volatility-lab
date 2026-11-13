"""Read-only local diagnostics. No network, directory creation or DB repair."""

from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from contextlib import closing


def _age(value, now):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Undated timestamp")
    age = (now - stamp).total_seconds()
    if age < -30:
        raise ValueError("Future timestamp")
    return max(0, age)


def inspect(settings, now=None):
    now = now or datetime.now(timezone.utc)
    checks = []
    root = settings.data_dir
    checks.append({"component": "storage", "status": "ok" if root.is_dir() else "missing"})
    database = root / "monitor.sqlite"
    if not database.exists():
        checks.append({"component": "database", "status": "missing"})
    else:
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as con:
                con.execute("PRAGMA query_only=ON")
                count = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                checks.append({"component": "database", "status": "ok", "tables": count})
        except sqlite3.Error:
            checks.append({"component": "database", "status": "error", "reason": "Unreadable SQLite database"})
    for name, path, field, maximum in [
        ("snapshot", root / "expiry_latest.json", "timestamp", 120),
        ("archive_sync", root / "google_archive/status.json", "updated", 1200),
    ]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Invalid object")
            age = _age(value.get(field), now)
            status = "ok" if age <= maximum else "stale"
            if name == "archive_sync" and value.get("status") != "ready":
                status = "degraded"
            if name == "snapshot" and (
                value.get("provider") != "Upstox" or not isinstance(value.get("rows"), list) or not value["rows"]
            ):
                raise ValueError("Missing broker observations")
            checks.append({"component": name, "status": status, "age_seconds": round(age)})
        except FileNotFoundError:
            checks.append({"component": name, "status": "missing"})
        except (ValueError, TypeError, OSError, UnicodeError):
            checks.append({"component": name, "status": "error", "reason": "Invalid or undated status data"})
    if settings.archive_dir:
        checks.append({"component": "archive_source", "status": "ok" if settings.archive_dir.is_dir() else "missing"})
    return {
        "schema": 1,
        "checked_at": now.isoformat(),
        "environment": settings.environment,
        "offline": settings.offline,
        "status": "error"
        if any(c["status"] == "error" for c in checks)
        else "degraded"
        if any(c["status"] != "ok" for c in checks)
        else "ok",
        "checks": checks,
    }
