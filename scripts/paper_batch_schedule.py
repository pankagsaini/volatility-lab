"""Check whether a catch-up trigger still needs to run the paper worker."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess


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


def github(path):
    result = subprocess.run(["gh", "api", path], capture_output=True, check=True, timeout=12)
    return json.loads(result.stdout)


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
                    return run["id"]
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
