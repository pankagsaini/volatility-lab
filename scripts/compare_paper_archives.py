"""Compare two preserved historical paper exports without rewriting either book."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read_book(path):
    raw = path.read_bytes()
    book = json.loads(raw)
    return hashlib.sha256(raw).hexdigest(), book


def by_id(rows):
    records = {row["id"]: row for row in rows}
    if len(records) != len(rows) or any(not isinstance(key, str) or not key for key in records):
        raise ValueError("Missing or duplicate position ID")
    return records


def close_terms(row):
    fields = ("sym", "exp", "strat", "date", "gross", "charges", "net")
    leg_fields = ("strike", "type", "side", "mult", "entryPx", "exitPx")
    if any(row.get(k) is None for k in fields) or not row.get("legs"):
        return None
    if any(leg.get(k) is None for leg in row["legs"] for k in leg_fields):
        return None
    legs = sorted(tuple(leg[k] for k in leg_fields) for leg in row["legs"])
    return tuple(row[k] for k in fields), legs


def compare(left, right):
    rows = []
    for bucket in ("open_positions", "closed_trades"):
        a, b = by_id(left[bucket]), by_id(right[bucket])
        for key in sorted(a.keys() | b.keys()):
            if key not in a:
                finding = "right_only"
            elif key not in b:
                finding = "left_only"
            elif bucket == "open_positions":
                finding = "shared_open_id"
            else:
                left_terms, right_terms = close_terms(a[key]), close_terms(b[key])
                if left_terms is None or right_terms is None:
                    finding = "missing_recorded_terms"
                elif left_terms == right_terms:
                    finding = "same_recorded_terms"
                else:
                    finding = "conflicting_terms"
            rows.append({"bucket": bucket, "id": key, "finding": finding})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--show-ids", action="store_true", help="Include individual IDs in local output")
    args = parser.parse_args()
    left_hash, left = read_book(args.left)
    right_hash, right = read_book(args.right)
    rows = compare(left, right)
    result = {"left_sha256": left_hash, "right_sha256": right_hash,
              "counts": {bucket: dict(Counter(r["finding"] for r in rows if r["bucket"] == bucket))
                         for bucket in ("open_positions", "closed_trades")}}
    if args.show_ids:
        result["rows"] = rows
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
