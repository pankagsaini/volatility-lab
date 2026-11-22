"""Restore the prospective paper worker and prepare an atomic, data-only Git update."""

import argparse
import base64
from datetime import datetime
import gzip
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
from volatility_lab.backtest.hosted_paper import DriveReader, build_batch
from volatility_lab.runtime.settings import data_dir
from volatility_lab.storage.references import initialize_references
from scripts.paper_risk_history import install as install_risk_history

START = "2026-11-23"
# This data-only root commit armed the cohort on November 21. A later empty-looking
# export cannot replace an initialized private ledger, even after a lost checkpoint.
ARMED_COMMIT = "fb23030a0ff991a12156946b3172023b11c54d8c"
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
                              check=True, timeout=20).stdout.decode().strip()

    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Data worktree must be clean")
    head = git("rev-parse", "HEAD")
    expected = {PUBLIC} if head == ARMED_COMMIT else {PUBLIC, SEALED}
    entries = git("ls-tree", "HEAD").splitlines()
    if (any(not entry.startswith("100644 blob ") for entry in entries)
            or {entry.split("\t")[-1] for entry in entries} != expected):
        raise ValueError("Unexpected files or missing checkpoint on the data branch")
    return head


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
    if (state["cohort_kind"] != "prospective" or state["source_authority"] != worker.AUTHORITY
            or state["prospective_start"] != START or state["min_trigger_date"] != START
            or state["inception_date"] != START or state["policy_version"] != "research-v1"
            or cp.get("drive_folder_id") != folder or worker.public_state(state) != public):
        raise ValueError("Private checkpoint does not match the published cohort or source")
    return state


def prove_archive_access(reader, private):
    # Prove media download and schema validation on the first run, even before
    # prospective trading starts. This pre-cohort report never enters build_batch.
    previous = max((item for item in reader.list_captures("2026-01-01", "") if item.name[:10] < START),
                   key=lambda item: item.name, default=None)
    if previous is None or not worker.CAPTURE.fullmatch(previous.name):
        raise ValueError("Archive preflight requires a complete pre-cohort snapshot")
    path = private / previous.name
    try:
        reader.download(previous, path)
        _, digest = worker._read_report(path)
    finally:
        path.unlink(missing_ok=True)
    print(f"Verified pre-cohort archive media and report schema. SHA-256 {digest}", flush=True)


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
    metadata, candidate = build_batch(reader, work, START, max_captures=400)
    advanced = json.loads(ledger.read_bytes())
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
    except Exception as exc:
        # Drive responses, exception strings and filenames can identify private inputs.
        sys.exit(f"Paper batch failed while {stage} ({type(exc).__name__}).")


if __name__ == "__main__":
    main()
