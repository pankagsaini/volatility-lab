"""Offline checks for the Dhan backfill worker."""
import csv
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


if __name__ == "__main__":
    unittest.main()
