"""Install verified bundled reference data in an empty working store, without replacing user data."""

from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
import json
import os

from volatility_lab import PACKAGE_ROOT

REFERENCE_DIR = PACKAGE_ROOT / "data"


def text_digest(raw):
    # Git can check out text with native line endings. Hash its canonical LF representation.
    return sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def verified_references(source=REFERENCE_DIR):
    manifest = json.loads((source / "reference-manifest.json").read_text(encoding="utf-8"))
    if manifest["schema"] != 1:
        raise ValueError("Unsupported reference manifest")
    for name, expected in manifest["files"].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source.resolve()) or path.suffix not in (".json", ".csv", ".txt"):
            raise ValueError("Invalid reference artifact path")
        raw = path.read_bytes()
        if text_digest(raw) != expected:
            raise ValueError("Reference checksum differs for " + name)
        yield name, raw


def initialize_references(destination, source=REFERENCE_DIR):
    destination = Path(destination)
    if destination.resolve() == source.resolve():
        return
    # Validate the complete release before materializing a file. Publish a complete file
    # with atomic create-if-absent so concurrent launches and existing snapshots are safe.
    items = list(verified_references(source))
    for name, raw in items:
        path = destination / name
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temp = Path(temporary.name)
            temporary.write(raw)
        try:
            try:
                os.link(temp, path)
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)
