from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from scripts import paper_batch_schedule as schedule


def paper_book(last, start="2026-11-23"):
    return {"cohort_kind": "prospective", "source_authority": "python_paper_worker",
            "prospective_start": start, "last_capture_at": last}


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 27, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(schedule, "datetime", Clock)


@pytest.mark.parametrize(("cron", "created", "expected"), [
    ("41 5 * * *", "2026-11-23T05:41:15Z", "2026-11-23T05:19:00Z"),
    ("41 5 * * *", "2026-11-23T12:40:15Z", "2026-11-23T05:19:00Z"),
    ("41 10 * * *", "2026-11-23T16:11:10+05:30", "2026-11-23T10:19:00Z"),
    ("41 15 * * *", "2026-11-24T01:35:00Z", "2026-11-23T15:19:00Z"),
])
def test_paired_original_slot(cron, created, expected):
    assert schedule.paired_slot(cron, created) == schedule.utc(expected)


def test_skip_only_after_successful_processing_and_publication(clock):
    stamp = "2026-11-23T05:19:00Z"
    run = {"id": 3, "event": "schedule", "conclusion": "success", "head_branch": "master", "run_started_at": stamp}
    steps = [{"name": name, "conclusion": "success"} for name in schedule.REQUIRED_STEPS]
    job = {"name": "publish", "conclusion": "success", "steps": steps}
    skipped = deepcopy(job)
    next(s for s in skipped["steps"] if s["name"] == "Confirm archive backlog drained")["conclusion"] = "skipped"
    jobs = {2: skipped, 3: job}
    runs = [dict(run, id=1, run_started_at="2026-11-23T05:18:59Z"), dict(run, id=2),
            dict(run, id=4, event="pull_request"), dict(run, id=5, head_branch="feature"), dict(run, id=6, conclusion="failure")]

    book = paper_book("2026-11-23T10:48:20+05:30")

    def read(path, *, raw=False):
        if path == "repos/fixture/repo/contents/paper-ledger.json?ref=paper-feed":
            assert raw
            return book
        if path.endswith("/runs/99"):
            return {"created_at": "2026-11-23T05:45:22Z"}
        if "workflows/paper-batch.yml/runs?" in path:
            return {"workflow_runs": runs}
        return {"jobs": [jobs[int(path.split("/")[-2])]]}

    assert schedule.completed_batch("41 5 * * *", 99, "fixture/repo", read) is None
    for event in ("schedule", "workflow_dispatch"):
        runs.append(dict(run, event=event))
        assert schedule.completed_batch("41 5 * * *", 99, "fixture/repo", read) == 3
        runs.pop()
    runs.append(run)
    book.update(last_capture_at="2026-11-23T09:25:00+05:30", updated_at="2026-11-23T11:10:00+05:30")
    assert schedule.completed_batch("41 5 * * *", 99, "fixture/repo", read) is None


@pytest.mark.parametrize(("slot", "start", "last", "covered"), [
    ("2026-11-23T10:49", "2026-11-23", None, False),
    ("2026-11-23T10:49", "2026-11-23", "2026-11-23T10:47:59", False),
    ("2026-11-23T10:49", "2026-11-23", "2026-11-23T10:48:00", True),
    ("2026-11-23T20:49", "2026-11-23", "2026-11-23T15:39:59", False),
    ("2026-11-23T20:49", "2026-11-23", "2026-11-23T15:40:59", True),
    ("2026-11-24T10:49", "2026-11-23", "2026-11-23T09:55:00", False),
    ("2026-11-24T10:49", "2026-11-23", "2026-11-23T15:40:00", True),
    ("2026-11-23T09:10", "2026-11-20", "2026-11-20T15:39:59", False),
    ("2026-11-23T09:10", "2026-11-20", "2026-11-20T15:40:00", True),
    ("2026-11-23T09:10", "2026-11-23", None, True),
    ("2026-11-08T10:49", "2026-11-06", "2026-11-06T15:40:00", True),
    ("2026-11-08T15:49", "2026-11-06", "2026-11-06T15:40:00", False),
    ("2026-11-08T15:49", "2026-11-06", "2026-11-08T15:40:00", True),
    ("2026-11-23T10:49", "2026-11-23", "2026-11-23T23:00:00", False),
    ("2026-11-23T10:49", "2026-11-23", "2026-11-30T10:48:00", False),
])
def test_published_coverage_for_paired_slot(clock, slot, start, last, covered):
    last = last + "+05:30" if last else None
    assert schedule.published_through_slot(paper_book(last, start), schedule.utc(slot + "+05:30")) is covered


@pytest.mark.parametrize(("slot", "start", "last"), [
    ("2026-11-23T10:49", "2026-11-23", "2026-11-23T10:48:00"),
    ("2026-11-23T10:49", "malformed", None),
    ("2024-11-01T15:49", "2024-10-31", None),  # Special date without verified hours.
    ("2027-01-05T10:49", "2026-11-23", None),  # Only provisional holiday data.
])
def test_unusable_timing_cannot_establish_coverage(slot, start, last):
    with pytest.raises(ValueError):
        schedule.published_through_slot(paper_book(last, start), schedule.utc(slot + "+05:30"))


@pytest.mark.parametrize("error", [
    subprocess.TimeoutExpired("gh", 12),
    ValueError("invalid public ledger timestamp"),
    KeyError("last_capture_at"),
])
def test_unavailable_history_leaves_worker_enabled(tmp_path, monkeypatch, error):
    for name in ("PAPER_BATCH_CRON", "GITHUB_RUN_ID", "GITHUB_REPOSITORY"):
        monkeypatch.setenv(name, "fixture")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    def unavailable(*args):
        raise error

    monkeypatch.setattr(schedule, "completed_batch", unavailable)
    schedule.main()
    assert not output.exists()


def test_workflow_checks_only_catchups_and_gates_private_steps():
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/paper-batch.yml").read_text()
    for cron in schedule.CATCHUP_HOURS:
        assert f"- cron: '{cron}'" in workflow
    assert "- cron: '19 5,10,15 * * *'" in workflow
    assert "github.event_name == 'schedule' && startsWith(github.event.schedule, '41 ')" in workflow
    for step in schedule.REQUIRED_STEPS - {"Confirm archive backlog drained"}:
        assert f"- name: {step}\n        if: steps.catchup.outputs.skip != 'true'" in workflow
    assert "- name: Confirm archive backlog drained\n        if: steps.process.outputs.caught_up == 'true'" in workflow


@pytest.mark.parametrize(("more", "pending", "expected"), [
    (False, None, "true"),
    (True, None, "false"),
    (False, "unsettled.json.gz", "false"),
])
def test_only_an_exhausted_archive_can_suppress_recovery(tmp_path, monkeypatch, more, pending, expected):
    from scripts.build_paper_feed import report_archive_backlog

    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    report_archive_backlog({"more": more, "pending": pending})
    assert output.read_text().strip() == f"caught_up={expected}"
