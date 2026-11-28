"""Offline checks for the Dhan backfill worker."""
import csv
import errno
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import dhan_options_downloader as worker


class DownloadTests(unittest.TestCase):
    def test_universe_and_dates(self):
        periods = list(worker.windows(worker.START, worker.END))
        self.assertEqual(len(periods), 61)
        self.assertEqual(periods[-1][1], date(2026, 11, 27))
        for symbol, count in (("NIFTY", 141), ("BANKNIFTY", 71)):
            selectors = list(worker.series(symbol, *periods[0], False))
            self.assertEqual(len(selectors), count)
            flags = {req["expiryFlag"] for _, _, req in selectors[1:]}
            self.assertEqual(flags, set(worker.OPTION_CYCLES[symbol]))
            self.assertTrue(all("iv" in req["requiredData"] for _, _, req in selectors[1:]))

    def test_resumed_coverage_counts_completed_windows(self):
        left, right = date(2022, 1, 1), date(2022, 1, 31)
        columns = ("symbol window_start window_end_exclusive series rows positive_iv null_iv nonpositive_iv "
                   "missing_strike first_epoch last_epoch spot_days_without_series series_days_without_spot "
                   "missing_spot_minutes off_spot_minutes").split()
        mirror = Mock()
        mirror.has_remote_zip.return_value = True
        with tempfile.TemporaryDirectory() as directory, patch.object(worker, "DRIVE_MIRROR", mirror), \
                patch.object(worker, "START", left), patch.object(worker, "END", right):
            root = Path(directory)
            history = root / f"history-v1_{left}_{right}"
            history.mkdir()
            with (history / "BANKNIFTY_coverage.csv").open("w", newline="") as report:
                writer = csv.DictWriter(report, columns)
                writer.writeheader()
                for key, _, _ in worker.series("BANKNIFTY", left, right, False):
                    writer.writerow(dict(symbol="BANKNIFTY", window_start=left, window_end_exclusive=right,
                                         series=key, rows=2, positive_iv="" if key == "spot" else 2))
            summary = worker.run_download(root, None, False, "BANKNIFTY")
            self.assertEqual((summary["series_windows"], summary["series_rows"],
                              summary["positive_iv_observations"]), (71, 142, 140))
            mirror.restore_archive.assert_not_called()
            mirror.upload_summary.assert_called_once()

    def test_temporary_storage_outage_preserves_operation(self):
        action = Mock(side_effect=[OSError(errno.EIO, "mount"), b"ok"])
        with patch.object(worker, "sleep") as wait:
            self.assertEqual(worker.retry_drive_io(action), b"ok")
            wait.assert_called_once_with(5)
        with self.assertRaises(OSError), patch.object(worker, "sleep") as wait:
            worker.retry_drive_io(Mock(side_effect=OSError(errno.ENOSPC, "full")))
        wait.assert_not_called()

    def test_corrupt_day_and_valid_legacy_day(self):
        start, end = date(2022, 1, 3), date(2022, 1, 5)
        _, _, request = list(worker.series("NIFTY", start, end, False))[1]
        wire = dict(request, securityId=int(request["securityId"]), expiryCode=int(request["expiryCode"]))
        first, second = "2022-01-03", "2022-01-04"

        def response(day):
            epoch = int(worker.datetime.fromisoformat(day).replace(tzinfo=worker.IST).timestamp())
            return {"data": {"ce": {"timestamp": [epoch], **{k: [1] for k in worker.OPTION_FIELDS}}}}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / (first + ".json")).write_text(json.dumps(
                dict(request=dict(wire, fromDate=first, toDate=second), response=response(first))))
            (path / (second + ".json")).write_text("{bad")
            with patch.object(worker, "call_api", return_value=response(second)) as api:
                result = worker.get_option_window(None, request, [first, second], path, repair=True)
                self.assertEqual(api.call_count, 1)
                self.assertEqual(api.call_args.args[2]["fromDate"], second)
            self.assertEqual(len(result["response"]["data"]["ce"]["timestamp"]), 2)
            self.assertEqual(len(list(path.glob("*.rejected"))), 1)
            self.assertEqual(len(list(path.glob("*.batch"))), 1)
            with patch.object(worker, "call_api", side_effect=AssertionError("duplicate request")):
                worker.get_option_window(None, request, [first, second], path, repair=True)

    def test_final_repair_after_both_symbols(self):
        from collections import Counter
        left, right = date(2022, 1, 3), date(2022, 1, 5)
        counts = Counter()
        visits = []
        def collect(folder, symbol, start, stop, pilot, session, repair=False):
            visits.append((symbol, repair))
            counts[symbol] += 1
            if symbol == "NIFTY" and not repair:
                raise worker.IncompleteOptionWindow({"MONTH_1_ATM_CALL": "invalid response"})
            return {"spot": dict(dates={}, rows=1, positive_iv=None, null_iv=None,
                                 nonpositive_iv=None, missing_strike=0, first=1, last=1)}
        with tempfile.TemporaryDirectory() as tmp, patch.object(worker, "START", left), \
                patch.object(worker, "END", right), patch.object(worker, "collect_bundle", side_effect=collect):
            root = Path(tmp)
            worker.save_json(root / "options-progress.json",
                             {s: {"pilot": {"ready_for_history": True}} for s in worker.SYMBOLS})
            report = worker.download_options(root, None)
            self.assertEqual(visits, [("NIFTY", False), ("BANKNIFTY", False), ("NIFTY", True)])
            self.assertEqual(json.loads((root / "options-pending.json").read_text()), {})
            self.assertEqual(report["NIFTY"]["history"]["status"], "staged_pending_review")

    def test_rate_limit_slows_shared_pacer(self):
        with tempfile.TemporaryDirectory() as tmp:
            pacer = worker.DownloadPacer(Path(tmp) / "ledger.json")
            self.assertEqual(pacer.interval, 0.25)
            pacer.throttle()
            self.assertGreaterEqual(pacer.interval, 0.5)
            self.assertEqual(worker.DAILY_BUDGET, 100_000)


if __name__ == "__main__":
    unittest.main()
