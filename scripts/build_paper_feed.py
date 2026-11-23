"""Restore the prospective paper worker and prepare an atomic, data-only Git update."""

import argparse
import base64
from datetime import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from volatility_lab.backtest import paper_worker as worker
from volatility_lab.backtest.cohort_performance import format_outcomes, prospective_outcomes
from volatility_lab.backtest.hosted_paper import DriveReader, MissingPriorClose, build_batch
from volatility_lab.backtest.session_evidence import format_session_evidence
from volatility_lab.core.freeze_policy import RULES
from volatility_lab.runtime.settings import data_dir
from volatility_lab.storage.references import initialize_references
from scripts.paper_collector_status import collector_runtime_summary
from scripts.paper_risk_history import install as install_risk_history

START = "2026-11-23"
# This data-only root commit armed the cohort on November 21. A later empty-looking
# export cannot replace an initialized private ledger, even after a lost checkpoint.
ARMED_COMMIT = "fb23030a0ff991a12156946b3172023b11c54d8c"
# Exact Git blob bytes of the first public book, and canonical JSON of the
# entry rules in effect when it was armed (source commit f3419a7).
INCEPTION_EXPORT_SHA256 = "c2a7de1f40f0dff4b2b89061424ea67b93f493a33dc323d1ddcbaee78d74f9ed"
ENTRY_RULES_SHA256 = "fae5312717570f10878b4b64feb0a367deb1c7c16b4438337aa93eae0c1f2941"
PUBLIC = "paper-ledger.json"
SEALED = "paper-checkpoint.gpg"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
MAX_SEALED = 48 * 1024 * 1024
MAX_PLAIN = 256 * 1024 * 1024


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def drive_token(credential, private):
    if (credential.get("type") != "service_account" or credential.get("token_uri") != TOKEN_URI
            or not credential["client_email"].endswith(".gserviceaccount.com")):
        raise ValueError("Invalid Drive reader configuration")
    issued = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": credential["private_key_id"]}
    claims = {"iss": credential["client_email"], "scope": SCOPE, "aud": TOKEN_URI,
              "iat": issued, "exp": issued + 3600}
    unsigned = base64url(compact(header)) + b"." + base64url(compact(claims))
    key_file = private / "reader.pem"
    try:
        key_file.write_bytes(credential["private_key"].encode())
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key_file)],
                                   input=unsigned, capture_output=True, check=True, timeout=20).stdout
    finally:
        key_file.unlink(missing_ok=True)
    with requests.post(TOKEN_URI, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": (unsigned + b"." + base64url(signature)).decode("ascii"),
    }, timeout=(10, 30), allow_redirects=False) as response:
        if response.status_code != 200:
            raise RuntimeError("Drive reader authentication failed")
        return response.json()["access_token"]


def gpg(private, key, source, output, *, encrypt):
    home = private / "gnupg"
    home.mkdir(mode=0o700, exist_ok=True)
    options = (["--cipher-algo", "AES256", "--compress-algo", "none", "--s2k-digest-algo", "SHA256",
                "--symmetric"] if encrypt else ["--max-output", str(MAX_SEALED), "--decrypt"])
    result = subprocess.run(["gpg", "--no-options", "--homedir", home.name, "--batch", "--yes",
                             "--no-symkey-cache", "--pinentry-mode", "loopback", "--passphrase-fd", "0",
                             "--output", output.name, *options, source.name],
                            cwd=private, input=key + b"\n", capture_output=True, timeout=120)
    if result.returncode or not output.is_file() or output.stat().st_size > MAX_SEALED:
        raise ValueError("Checkpoint encryption or authentication failed")


def read_checkpoint(sealed, private, key):
    if not 0 < sealed.stat().st_size <= MAX_SEALED:
        raise ValueError("Invalid encrypted checkpoint size")
    if sealed.parent != private:
        sealed = Path(shutil.copyfile(sealed, private / "incoming.gpg"))
    packed = private / "restored.gz"
    gpg(private, key, sealed, packed, encrypt=False)
    with gzip.open(packed, "rb") as stream:
        raw = stream.read(MAX_PLAIN + 1)
    if len(raw) > MAX_PLAIN:
        raise ValueError("Checkpoint exceeds the worker size limit")
    return json.loads(raw)


def feed_head(feed):
    def git(*args):
        return subprocess.run(["git", "-C", str(feed), *args], capture_output=True,
                              check=True, timeout=20).stdout

    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Data worktree must be clean")
    head = git("rev-parse", "HEAD").decode().strip()
    if git("rev-list", "--max-parents=0", head).decode().splitlines() != [ARMED_COMMIT]:
        raise ValueError("Data history does not descend from the prospective inception commit")
    if hashlib.sha256(git("show", f"{ARMED_COMMIT}:{PUBLIC}")).hexdigest() != INCEPTION_EXPORT_SHA256:
        raise ValueError("Prospective inception export differs from the frozen book")
    if hashlib.sha256(compact(RULES)).hexdigest() != ENTRY_RULES_SHA256:
        raise ValueError("Active entry rules differ from the frozen prospective policy")
    expected = {PUBLIC} if head == ARMED_COMMIT else {PUBLIC, SEALED}
    entries = git("ls-tree", "HEAD").decode().splitlines()
    if (any(not entry.startswith("100644 blob ") for entry in entries)
            or {entry.split("\t")[-1] for entry in entries} != expected):
        raise ValueError("Unexpected files or missing checkpoint on the data branch")
    return head


def verify_trade_lineage(state):
    """Only archive-backed trades of this prospective policy may enter its book."""
    if (state.get("cohort_kind") != "prospective" or state.get("source_authority") != worker.AUTHORITY
            or state.get("policy_version") != "research-v1" or any(state.get(k) != START for k in
                ("inception_date", "prospective_start", "min_trigger_date"))):
        raise ValueError("Ledger does not belong to the frozen prospective cohort")
    cursor = state["worker_checkpoint"]["cursor"]
    if any(not START <= (point.get("session_date") or "") <= cursor[:10] for point in state["intraday_mtm_series"]):
        raise ValueError("Portfolio mark falls outside the prospective archive")
    seen = set()
    for bucket, prefix in (("open_positions", "OPEN-"), ("closed_trades", "FWD-")):
        for trade in state[bucket]:
            trade_id = trade.get("id", "")
            identity = trade_id.removeprefix(prefix)
            if not identity or not trade_id.startswith(prefix) or identity in seen:
                raise ValueError("Duplicate or foreign prospective position identity")
            seen.add(identity)
            holding = RULES.get(trade.get("strat"), {}).get("holding")
            if (trade.get("policy_version") != "research-v1" or holding not in ("intraday", "positional")
                    or trade.get("holding") != holding):
                raise ValueError("Trade does not belong to the frozen prospective policy")
            endpoints = [("entry", "triggerTime", "session_date")]
            if bucket == "closed_trades":
                endpoints.append(("exit", "exit_at", "exit_session_date"))
            for label, when, day_field in endpoints:
                name = trade.get(f"{label}_capture") or ""
                digest = trade.get(f"{label}_capture_sha256") or ""
                stamp = worker.engine._parse_aware_timestamp(trade.get(when))
                if (not worker.CAPTURE.fullmatch(name) or name[:10] < START or name > cursor
                        or not stamp or name[:10] != stamp.date().isoformat() or name[:10] != trade.get(day_field)
                        or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
                    raise ValueError(f"Trade lacks a prospective {label} capture")
            if bucket == "closed_trades" and trade["exit_capture"] < trade["entry_capture"]:
                raise ValueError("Trade exit precedes its prospective entry")


def restore(feed, head, private, key, public, folder):
    if head == ARMED_COMMIT:
        # This exact Git object contains the original empty public export and no
        # private trading state. Its committed cohort has already been armed.
        state = dict(public)
        del state["capture_audit"]
        state["worker_checkpoint"] = {"cursor": "", "completed": 0, "quarantine": [],
                                      "last_day": None, "drive_folder_id": folder}
    else:
        state = read_checkpoint(feed / SEALED, private, key)
    worker.engine._validate_ledger(state)
    cp = state["worker_checkpoint"]
    if cp.get("drive_folder_id") != folder or worker.public_state(state) != public:
        raise ValueError("Private checkpoint does not match the published cohort or source")
    verify_trade_lineage(state)
    return state


def prove_archive_access(reader, private):
    # Prove media download and schema validation on the first run, even before
    # prospective trading starts. This pre-cohort report never enters build_batch.
    previous = max((item for item in reader.list_captures("2026-01-01", "")
                    if item.name[:10] < START and worker.CAPTURE.fullmatch(item.name)),
                   key=lambda item: item.name, default=None)
    if previous is None:
        raise ValueError("Archive preflight requires a complete pre-cohort snapshot")
    path = private / previous.name
    try:
        reader.download(previous, path)
        _, digest = worker._read_report(path)
    finally:
        path.unlink(missing_ok=True)
    print(f"Verified pre-cohort archive media and report schema. SHA-256 {digest}", flush=True)


def verify_position_continuity(prior, advanced):
    """A position can be marked or closed, but its original legs and books must survive."""
    def entry(trade):
        identity = tuple(trade.get(field) for field in (
            "session_date", "triggerTime", "sym", "exp", "strat", "holding", "policy_version", "lots",
            "available_at", "scheduled_exit_at", "entry_capture", "entry_capture_sha256",
        ))
        legs = [tuple(leg.get(field) for field in ("strike", "type", "side", "mult", "entryPx", "entryQuoteAt"))
                for leg in trade["legs"]]
        return identity, legs

    successors = {p["id"].split("-", 1)[1]: p for p in advanced["open_positions"] + advanced["closed_trades"]}
    for position in prior["open_positions"] + prior["closed_trades"]:
        successor = successors.get(position["id"].split("-", 1)[1])
        if successor is None or entry(position) != entry(successor):
            raise ValueError("Publication would lose a position or change its original entry")
        if position["id"].startswith("FWD-") and any(
            position[k] != successor.get(k) for k in ("id", "exit_capture", "exit_capture_sha256")
        ):
            raise ValueError("Publication would change a closed position's capture evidence")


def verify_progress(previous, candidate):
    audit = candidate["capture_audit"]
    if any(audit[k] < previous["capture_audit"][k] for k in ("processed", "exceptions")):
        raise ValueError("Publication would undo capture accounting")
    old_time = previous["last_capture_at"]
    new_time = candidate["last_capture_at"]
    if old_time and (not new_time or datetime.fromisoformat(new_time) < datetime.fromisoformat(old_time)):
        raise ValueError("Publication would move the captured time backwards")
    closes = {trade["id"]: trade for trade in candidate["closed_trades"]}
    if len(closes) != len(candidate["closed_trades"]) or any(
        closes.get(trade["id"]) != trade for trade in previous["closed_trades"]
    ):
        raise ValueError("Publication would overwrite a closed trade")


def write_summary(text):
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            print(text, file=output)


def update_feed(feed, private, key, reader):
    if len(key) < 32 or b"\n" in key or b"\r" in key or b"\x00" in key:
        raise ValueError("Use a single-line checkpoint key of at least 32 bytes")
    head = feed_head(feed)
    previous = json.loads((feed / PUBLIC).read_bytes())
    state = restore(feed, head, private, key, previous, reader.folder_id)
    if head == ARMED_COMMIT:
        prove_archive_access(reader, private)
    work = private / "worker"
    work.mkdir()
    ledger = work / worker.engine.LEDGER_FILENAME
    ledger.write_bytes(compact(state))
    try:
        metadata, candidate = build_batch(reader, work, START, max_captures=400)
    except MissingPriorClose as exc:
        if getattr(exc, "session_evidence", None):
            write_summary(format_session_evidence(exc.session_evidence))
        write_summary("The prior-session risk inputs were unavailable. The published checkpoint remains at its previous capture.")
        raise
    advanced = json.loads(ledger.read_bytes())
    verify_trade_lineage(advanced)
    verify_position_continuity(state, advanced)
    if candidate != worker.public_state(advanced):
        raise ValueError("Public projection differs from private checkpoint")
    verify_progress(previous, candidate)
    if advanced["worker_checkpoint"]["cursor"] < state["worker_checkpoint"]["cursor"]:
        raise ValueError("Checkpoint cursor moved backwards")
    if head == ARMED_COMMIT or advanced != state:
        raw = compact(advanced)
        if len(raw) > MAX_PLAIN:
            raise ValueError("Checkpoint exceeds the worker size limit")
        packed = private / "ledger.gz"
        packed.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
        sealed = private / SEALED
        gpg(private, key, packed, sealed, encrypt=True)
        if read_checkpoint(sealed, private, key) != advanced:
            raise ValueError("Encrypted checkpoint round-trip verification failed")
        shutil.copyfile(sealed, feed / SEALED)
        if previous != candidate:
            (feed / PUBLIC).write_bytes(compact(candidate) + b"\n")
    write_summary(format_session_evidence(metadata["session_evidence"]) + "\n" + format_outcomes(prospective_outcomes(candidate)))
    print(f"Processed {int(metadata['processed'])} captures. Pending capture: {bool(metadata['pending'])}. "
          f"Additional captures queued: {bool(metadata['more'])}.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feed", type=Path, help="Clean detached worktree of the paper-feed branch")
    args = parser.parse_args()
    stage = "loading the dedicated reader and checkpoint secrets"
    try:
        key = os.environ.pop("PAPER_CHECKPOINT_KEY").encode()
        credential = json.loads(os.environ.pop("PAPER_DRIVE_READER_JSON"))
        folder = os.environ.pop("PAPER_DRIVE_FOLDER_ID")
        with tempfile.TemporaryDirectory(prefix="paper-private-") as tmp:
            private = Path(tmp)
            stage = "installing verified release references"
            initialize_references(data_dir())
            stage = "verifying daily risk histories from the published site"
            install_risk_history(data_dir())
            stage = "authenticating the read-only Drive account"
            reader = DriveReader(folder, drive_token(credential, private))
            stage = "verifying the feed, archive, and next checkpoint"
            update_feed(args.feed.resolve(), private, key, reader)
            write_summary(collector_runtime_summary())
    except Exception as exc:
        # Drive responses, exception strings and filenames can identify private inputs.
        sys.exit(f"Paper batch failed while {stage} ({type(exc).__name__}).")


if __name__ == "__main__":
    main()
