"""Check whether a catch-up trigger still needs to run the paper worker."""

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from volatility_lab.core.market_calendar import IST, LAST_SOURCED_HOLIDAY_YEAR, is_trading_day, session_window


CATCHUP_HOURS = {f"41 {hour} * * *": hour for hour in (5, 10, 15)}
REQUIRED_STEPS = {
    "Process archived captures in a private temporary directory",
    "Publish one fast-forward commit",
    "Confirm archive backlog drained",
}


def utc(stamp):
    value = datetime.fromisoformat(stamp)
    if value.utcoffset() is None:
        raise ValueError("Missing GitHub run timezone")
    return value.astimezone(timezone.utc)


def paired_slot(cron, created_at):
    # The runner can start much later than dispatch. Each catch-up expression
    # identifies its UTC hour even when GitHub delivers it after midnight.
    created = utc(created_at)
    targeted = created.replace(hour=CATCHUP_HOURS[cron], minute=41, second=0, microsecond=0)
    if targeted > created:
        targeted -= timedelta(days=1)
    return targeted - timedelta(minutes=22)


def github(path, *, raw=False):
    args = ["gh", "api", path]
    if raw:
        args.extend(["--header", "Accept: application/vnd.github.raw+json"])
    result = subprocess.run(args, capture_output=True, check=True, timeout=12)
    return json.loads(result.stdout)


def required_capture(due, inception):
    """Last completed trading minute due at this slot since the cohort opened."""
    day = due.astimezone(IST).date()
    while day >= inception:
        if day.year > LAST_SOURCED_HOLIDAY_YEAR:
            raise ValueError("Holiday calendar is provisional")
        window = session_window(day)
        if window:
            target = min(due - timedelta(minutes=1), window[1])
            if target >= window[0]:
                return target
        elif is_trading_day(day, include_special=True):
            raise ValueError("Trading session has no verified hours")
        day -= timedelta(days=1)
    return None


def published_through_slot(book, due):
    if book["cohort_kind"] != "prospective" or book["source_authority"] != "python_paper_worker":
        return False
    target = required_capture(due, date.fromisoformat(book["prospective_start"]))
    if target is None:
        return True
    if not book["last_capture_at"]:
        return False
    observed = utc(book["last_capture_at"]).astimezone(IST)
    minute = observed.replace(second=0, microsecond=0)
    window = session_window(minute.date())
    return (window is not None and window[0] <= minute <= window[1]
            and target <= observed <= datetime.now(timezone.utc))


def completed_batch(cron, run_id, repo, read=github):
    api = f"repos/{repo}/actions"
    current = read(f"{api}/runs/{run_id}")
    due = paired_slot(cron, current["created_at"])
    runs = read(f"{api}/workflows/paper-batch.yml/runs?branch=master&status=success&per_page=50")
    for run in runs["workflow_runs"]:
        if (run["event"] not in ("schedule", "workflow_dispatch") or run["conclusion"] != "success"
                or run["head_branch"] != "master" or utc(run["run_started_at"]) < due):
            continue
        jobs = read(f"{api}/runs/{int(run['id'])}/jobs?filter=latest")
        for job in jobs["jobs"]:
            # A green run may have skipped processing or published only part of its backlog.
            if job["name"] == "publish" and job["conclusion"] == "success":
                steps = {step["name"] for step in job["steps"] if step["conclusion"] == "success"}
                if REQUIRED_STEPS <= steps:
                    book = read(f"repos/{repo}/contents/paper-ledger.json?ref=paper-feed", raw=True)
                    return run["id"] if published_through_slot(book, due) else None
    return None


def main():
    try:
        completed = completed_batch(os.environ["PAPER_BATCH_CRON"], os.environ["GITHUB_RUN_ID"],
                                    os.environ["GITHUB_REPOSITORY"])
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError):
        print("Could not verify a previous completed batch. Running the paper worker.")
        return
    if completed is None:
        print("No completed paper batch verified for this slot. Running the paper worker.")
        return
    message = f"Catch-up skipped. Run {completed} processed the archive and finished publication for this slot."
    print(message)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        print(message, file=summary)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        print("skip=true", file=output)


if __name__ == "__main__":
    main()
