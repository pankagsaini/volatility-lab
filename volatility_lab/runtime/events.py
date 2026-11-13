"""Small structured event schema; payload bodies and credentials are never logged."""

from datetime import datetime, timezone
import json
import logging
import re

FIELDS = {
    "component",
    "operation",
    "status",
    "attempt",
    "http_status",
    "duration_ms",
    "error_type",
    "records",
    "age_seconds",
}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                **getattr(record, "event_fields", {"event": "internal_log"}),
            },
            allow_nan=False,
        )


def configure(level="INFO"):
    logger = logging.getLogger("volatility_lab")
    if not any(getattr(h, "volatility_handler", False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.volatility_handler = True
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def emit(event, **values):
    if not re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", event):
        raise ValueError("Invalid event name")
    fields = {"event": event}
    for name, value in values.items():
        if name not in FIELDS:
            continue
        if isinstance(value, (int, bool)) or value is None:
            fields[name] = value
        elif isinstance(value, float):
            import math

            if math.isfinite(value):
                fields[name] = round(value, 3)
        elif isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
            fields[name] = value
    logging.getLogger("volatility_lab").info(event, extra={"event_fields": fields})
