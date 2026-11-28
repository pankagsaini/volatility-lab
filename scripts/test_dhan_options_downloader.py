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


class RecoveryTests(unittest.TestCase):
    left, right = date(2022, 1, 3), date(2022, 1, 5)
    first = int(worker.datetime(2022, 1, 3, 9, 15, tzinfo=worker.IST).timestamp())

    @staticmethod
    def candles(times, req=None, iv=None, strike=100):
        fields = worker.OPTION_FIELDS if req else worker.SPOT_FIELDS
        values = {k: [1] * len(times) for k in fields}
        values["timestamp"] = times
        if req:
            values["iv"] = iv if iv is not None else [10] * len(times)
            values["strike"] = [strike] * len(times)
            return {"data": {"ce" if req["drvOptionType"] == "CALL" else "pe": values}}
        return values

    def test_archive_repair_on_resume_keeps_raw_and_updates_coverage(self):
        from zipfile import ZipFile
        from contextlib import redirect_stdout
        from io import StringIO
        ts = self.first
        times = [ts, ts+60, ts+86400]
        original_series = worker.series
        def scope(*args):
            return list(original_series(*args))[:3]
        def original(_, endpoint, req):
            if endpoint.endswith("intraday"):
                return self.candles(times)
            return self.candles([ts, ts+86400], req, [None, 9])
        calls = []
        def daily(_, endpoint, req):
            calls.append((req["drvOptionType"], req["fromDate"]))
            self.assertEqual(req["toDate"], "2022-01-04")
            if req["drvOptionType"] == "PUT":
                return self.candles([ts], req, [15], strike=200)
            return self.candles([ts, ts+60], req, [12, 11])
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()), \
                patch.object(worker, "SYMBOLS", {"NIFTY":13}), patch.object(worker, "START", self.left), \
                patch.object(worker, "END", self.right), patch.object(worker, "series", side_effect=scope):
            root = Path(tmp)
            worker.save_json(root/"options-progress.json", {"NIFTY":{"pilot":{"ready_for_history":True}}})
            history = root / f"history-v1_{self.left}_{self.right}"
            history.mkdir()
            with patch.object(worker, "call_api", side_effect=original):
                worker.collect_bundle(history, "NIFTY", self.left, self.right, False, Mock(headers={}))
            with patch.object(worker, "call_api", side_effect=daily):
                report = worker.download_options(root, Mock(headers={}))
            self.assertCountEqual(calls, [("CALL", str(self.left)), ("PUT", str(self.left))])
            self.assertTrue(worker.integrity_finished(report))
            audit = report["NIFTY"]["integrity"]
            self.assertEqual((audit["missing_spot_minutes"],audit["invalid_iv"],audit["retry_windows"]), (1,1,0))
            self.assertFalse(report["NIFTY"]["history"]["passed"])
            with ZipFile(next(history.glob("*.zip"))) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                for key, sha in manifest["sha256"].items():
                    self.assertEqual(worker.digest(archive.read(key+".json")), sha)
                key, _, req = scope("NIFTY", self.left, self.right, False)[1]
                item = json.loads(archive.read(key+".json"))
                self.assertEqual(item["original_response"], original(None, "option", req))
                self.assertEqual(item["response"]["data"]["ce"]["iv"], [12,11,9])
                self.assertEqual(len(item["quality_responses"]), 1)
            with patch.object(worker, "call_api", side_effect=AssertionError("replayed completed window")):
                self.assertTrue(worker.integrity_finished(worker.download_options(root, Mock(headers={}))))

    def test_daily_failure_can_resume_without_redoing_successful_day(self):
        ts = self.first
        req = list(worker.series("NIFTY", self.left, self.right, False))[1][2]
        item = dict(request=req, endpoint="charts/rollingoption", response=self.candles([ts],req,[None]))
        spot = {ts, ts+86400}
        def daily(_, endpoint, payload):
            if payload["fromDate"] == str(self.left):
                return self.candles([], payload)
            raise worker.DhanHTTPError(endpoint, 422, "invalid-selector")
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with patch.object(worker, "call_api", side_effect=daily):
                _, errors = worker.repair_member(item, spot, None, cache)
            self.assertEqual(list(errors), ["2022-01-04"])
            with patch.object(worker, "call_api", return_value=self.candles([ts+86400], req)) as api:
                stats, errors = worker.repair_member(item, spot, None, cache)
            self.assertEqual((api.call_count, errors, stats["missing_spot_minutes"]), (1, {}, 0))

    def test_one_400_allows_next_window_and_other_selectors(self):
        from contextlib import redirect_stdout
        from io import StringIO
        original_series = worker.series
        seen = set()
        second = self.right
        end = date(2022, 1, 7)
        def scope(*args):
            return list(original_series(*args))[:3]
        def api(_, endpoint, req):
            ts = int(worker.datetime.fromisoformat(req["fromDate"][:10]).replace(tzinfo=worker.IST).timestamp())
            if endpoint.endswith("intraday"):
                return self.candles([ts])
            seen.add((req["fromDate"], req["drvOptionType"]))
            if req["fromDate"] == str(self.left) and req["drvOptionType"] == "CALL":
                raise worker.DhanHTTPError(endpoint, 400, "selector-rejected")
            return self.candles([ts], req)
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()), patch.object(worker, "START", self.left), \
                patch.object(worker, "END", end), patch.object(worker, "series", side_effect=scope), \
                patch.object(worker, "windows", return_value=[(self.left,second),(second,end)]), \
                patch.object(worker, "call_api", side_effect=api):
            result = worker.run_download(Path(tmp), Mock(headers={}), False, "NIFTY")
            self.assertIn((str(self.left),"PUT"), seen)
            self.assertIn((str(second),"CALL"), seen)
            self.assertEqual(result["incomplete_windows"][0]["unresolved_series"], [scope("NIFTY", self.left,second,False)[1][0]])
            self.assertTrue(list(Path(tmp).rglob(f"NIFTY_{second}_{end}.zip")))
            for status, code in [(400,"DH-901"), (403,"denied"), (429,"rate")]:
                self.assertFalse(worker.selector_error(worker.DhanHTTPError("",status,code)))

    def test_batch_digest_precedes_consumption(self):
        _, endpoint, req = list(worker.series("NIFTY",self.left,self.right,False))[1]
        old = dict(request=req, response=self.candles([self.first],req))
        raw = json.dumps([old]).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            batch = path / (worker.digest(raw)+".batch")
            batch.write_bytes(raw.replace(b'"close": [1]',b'"close": [999]'))
            with patch.object(worker,"call_api",return_value=self.candles([self.first],req)) as api:
                result = worker.get_option_window(None, req, [str(self.left)], path, repair=True)
            self.assertEqual(api.call_count, 1)
            self.assertEqual(worker.normalize_candles(result["response"],endpoint,req)[0]["close"], [1])
            self.assertTrue(list(path.glob(batch.name+".*.rejected")))

    def test_progress_and_queue_recover_without_resetting_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget = root/"options-request-budget.json"
            budget.write_bytes(b'{"123":900}')
            for name, entry in [("options-progress.json",{"NIFTY":{"pilot":{"ready_for_history":True}}}),
                              ("options-pending.json",{"NIFTY_2022-01-03_2022-01-05.zip":
                                  dict(symbol="NIFTY",start=str(self.left),end_exclusive=str(self.right))})]:
                path = root/name
                worker.save_json(path,entry)
                worker.save_json(path,{})
                path.write_bytes(b"{")
                self.assertEqual(worker.load_state(path),entry)
                path.write_bytes(b"[]")
                path.with_suffix(".json.bak").write_bytes(b'{"wrong":25}')
                self.assertEqual(worker.load_state(path),{})
            self.assertEqual(budget.read_bytes(),b'{"123":900}')

    def test_stale_successes_do_not_undo_cooldown_and_speed_recovers(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(worker,"monotonic",return_value=100) as clock:
            pacer = worker.DownloadPacer(Path(tmp)/"budget.json")
            generation = pacer.wait()
            pacer.throttle()
            pacer.cooldown(240)
            clock.return_value = 161
            for _ in range(64):
                pacer.success(generation)
            self.assertEqual(pacer.interval,0.5)
            deadline = pacer.next_start
            for tick in range(1,10):
                clock.return_value = 340+61*tick
                for _ in range(32):
                    pacer.success(pacer.generation)
            self.assertEqual(pacer.interval, worker.REQUEST_INTERVAL)
            self.assertEqual(pacer.next_start,deadline)

    def test_revision_guard_contention_and_legacy_handoff(self):
        from threading import Barrier, Lock, Thread
        barrier = Barrier(2)
        mutex = Lock()
        storage = {"value":"", "version":0}
        def read(**kwargs):
            def execute(**kwargs):
                with mutex:
                    version, value = storage["version"], storage["value"]
                if version == 0:
                    barrier.wait(timeout=5)
                return {"revisionId":str(version),"body":{"content":[{"paragraph":{"elements":[
                         {"textRun":{"content":value+"\n"}}]}}]}}
            return Mock(execute=execute)
        def update(documentId, body):
            def execute(**kwargs):
                with mutex:
                    if str(storage["version"]) != body["writeControl"]["requiredRevisionId"]:
                        raise RuntimeError("stale revision")
                    request = body["requests"][0]
                    storage["value"] = request.get("insertText",{}).get("text","")
                    storage["version"] += 1
            return Mock(execute=execute)
        docs = Mock(get=read, batchUpdate=update)
        results = []
        finish = Barrier(2)
        def start():
            with worker.exclusive_writer(docs,"id","test") as granted:
                results.append(granted)
                finish.wait(timeout=5)
        threads = [Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertCountEqual(results,[True,False])
        self.assertEqual(storage, {"value":"","version":2})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker.save_json(root/"options-run-status.json",dict(state="downloading",utc="old"))
            self.assertTrue(worker.legacy_busy(root))
            worker.save_json(root/"options-legacy-handoff.json",dict(stopped_status_utc="different"))
            self.assertTrue(worker.legacy_busy(root))
            worker.save_json(root/"options-legacy-handoff.json",dict(stopped_status_utc="old"))
            self.assertFalse(worker.legacy_busy(root))


if __name__ == "__main__":
    unittest.main()
