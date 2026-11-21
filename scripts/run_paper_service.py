"""Process the private Drive mirror and publish the prospective paper book to GitHub."""

import argparse
import base64
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from volatility_lab.backtest.paper_worker import AUTHORITY, PendingCapture, run_once  # noqa: E402
from volatility_lab.storage.collector_support import atomic_json, data_lock  # noqa: E402

CONTENTS = "repos/pankagsaini/volatility-lab/contents/paper-ledger.json"
BRANCH = "paper-feed"


def github(gh, *arguments, body=None):
    response = subprocess.run(
        [str(gh), "api", *arguments], input=body, capture_output=True, timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if response.returncode:
        # Never log a request body, credential or private snapshot.
        raise RuntimeError(f"GitHub paper feed request failed ({response.returncode})")
    return json.loads(response.stdout)


def publish(gh, artifact, receipt):
    payload = artifact.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if receipt.exists() and json.loads(receipt.read_bytes()).get("sha256") == digest:
        return False
    book = json.loads(payload)
    if book.get("source_authority") != AUTHORITY or book.get("cohort_kind") != "prospective":
        raise ValueError("Publication requires a prospective worker export")
    remote = github(gh, f"{CONTENTS}?ref={BRANCH}")
    # GET the blob separately when GitHub omits inline content on a larger export.
    encoded = remote.get("content") or github(gh, f"repos/pankagsaini/volatility-lab/git/blobs/{remote['sha']}")["content"]
    existing = json.loads(base64.b64decode(encoded))
    if (existing.get("source_authority") != AUTHORITY
            or existing.get("prospective_start") != book["prospective_start"]
            or existing.get("cohort_kind") != "prospective"):
        raise ValueError("The published feed belongs to another cohort")
    if (existing.get("last_capture_at") or "") > (book.get("last_capture_at") or ""):
        raise ValueError("Local capture is older than the published feed")
    if existing != book:
        closes = {row["id"]: row for row in book["closed_trades"]}
        if any(closes.get(row["id"]) != row for row in existing["closed_trades"]):
            raise ValueError("Local export would change published closes")
        update = {
            "branch": BRANCH, "sha": remote["sha"], "message": "Update prospective paper book",
            "content": base64.b64encode(payload).decode("ascii"),
        }
        github(gh, "--method", "PUT", CONTENTS, "--input", "-", body=json.dumps(update).encode())
    atomic_json(receipt, {"sha256": digest})
    return existing != book


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--gh", required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    home = args.state_dir.resolve()
    if home.is_relative_to(args.archive_dir.resolve()):
        parser.error("Keep worker state outside the collector archive")
    home.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(home / "service.log", maxBytes=500_000, backupCount=2)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[handler])
    artifact = home / "paper-ledger.json"
    with data_lock(lock_path=home / "publisher.lock", timeout=1):
        logging.info("Paper service started for cohort %s", args.start_date)
        last_report = 0
        while True:
            try:
                try:
                    state = run_once(args.archive_dir, home, args.start_date, artifact)
                except PendingCapture:
                    # A newer, still-syncing file must not suppress the last accepted mark.
                    publish(args.gh, artifact, home / "published.json")
                    raise
                changed = publish(args.gh, artifact, home / "published.json")
                if changed or time.monotonic() - last_report >= 900 or last_report == 0:
                    logging.info("Feed current through %s, processed %s captures",
                                 state.get("last_capture_at"), state["worker_checkpoint"]["completed"])
                    last_report = time.monotonic()
            except Exception:
                logging.exception("Paper service cycle failed")
                if args.once:
                    raise
            if args.once:
                return
            time.sleep(30)


if __name__ == "__main__":
    main()
