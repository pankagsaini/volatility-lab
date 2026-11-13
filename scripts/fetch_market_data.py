"""Inspect local storage, or explicitly hydrate it from validated Upstox captures."""

import argparse
from contextlib import closing
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from volatility_lab.runtime.settings import load_settings
from volatility_lab.runtime.health import inspect


def hydrate_database(settings):
    from volatility_lab.storage.references import initialize_references
    from volatility_lab.storage.collector_support import data_lock
    from volatility_lab.ingestion.google_archive_sync import validated, import_report
    from volatility_lab.services.expiry_data import db

    initialize_references(settings.data_dir)
    directory = settings.archive_dir or settings.data_dir / "google_archive"
    files = sorted(directory.glob("*_complete.json.gz"))
    imported = rejected = 0
    with data_lock(timeout=120):
        with closing(db()) as connection:
            # Early versions of this tool created a different column order and no primary key.
            # Leave an existing incompatible user database intact for explicit recovery.
            schema = connection.execute("PRAGMA table_info(expiry_observations)").fetchall()
            if [row[1] for row in schema] != ["date", "symbol", "expiry", "config", "iv", "spread", "source"] or sum(
                bool(row[5]) for row in schema
            ) != 4:
                raise ValueError(
                    "Legacy hydration schema detected. Preserve the database and seed a fresh VOL_LAB_DATA_DIR from verified captures."
                )
        for path in files:
            try:
                with path.open("rb") as handle:
                    report = validated(handle.read(20_000_001))
            except (OSError, EOFError, ValueError, TypeError):
                rejected += 1
                continue
            if report["mode"] == "snapshot":
                imported += import_report(report, rate=None, dividend=None, spread=3.0)
    return {"archives_examined": len(files), "invalid_archives": rejected, "broker_rows_processed": imported}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="Report local health (default)")
    mode.add_argument("--seed", action="store_true", help="Create schema and import validated broker archive records")
    args = parser.parse_args()
    settings = load_settings()
    if args.seed:
        print(json.dumps(hydrate_database(settings)))
    print(json.dumps(inspect(settings), indent=2))


if __name__ == "__main__":
    main()
