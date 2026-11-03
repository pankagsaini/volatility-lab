"""Manual launch safety without downloading or changing the real archive."""
from contextlib import redirect_stdout
from io import StringIO
import json
import msvcrt
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase,main
from unittest.mock import Mock,patch

import download_control as control


class DownloadControlTests(TestCase):
    def setUp(self):
        self.temporary=TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.archive=self.root/'data'/'backtest_1minute'
        self.archive.mkdir(parents=True)
        self.lock=self.archive/'download.lock'
        self.lock.write_bytes(b'0')

    def write_status(self,state='running',updated='2026-10-27T10:00:00+05:30',**extra):
        status=dict(status=state,updated=updated,**extra)
        (self.archive/'status.json').write_text(json.dumps(status),encoding='utf-8')
        return status

    def prepare_launcher(self):
        (self.root/'.runtime').mkdir()
        (self.root/'.runtime'/'pythonw.exe').write_bytes(b'test placeholder; never executed')
        (self.root/'backtest_download.py').write_text('# test placeholder',encoding='utf-8')

    def test_stale_running_status_does_not_establish_liveness(self):
        self.write_status(jobs={'done':1234,'pending':5,'retry':2,'failed':1})
        self.assertFalse(control.running(self.root))
        lines=control.status_lines(self.root)
        self.assertEqual(lines[0],'Download is NOT running.')
        self.assertIn('Completed requests: 1,234 | Pending: 5 | Retries: 2 | Failed: 1',lines)
        self.assertTrue(any('old running status was stale' in line for line in lines))

    def test_os_lock_establishes_liveness_even_if_saved_status_finished(self):
        self.write_status('finished_available_requests')
        with self.lock.open('r+b') as owner:
            msvcrt.locking(owner.fileno(),msvcrt.LK_NBLCK,1)
            try:
                self.assertTrue(control.running(self.root))
                self.assertEqual(control.status_lines(self.root)[0],'Download is RUNNING in the background.')
            finally:
                owner.seek(0)
                msvcrt.locking(owner.fileno(),msvcrt.LK_UNLCK,1)
        self.assertFalse(control.running(self.root))

    def test_missing_lock_and_invalid_status_are_safe_to_inspect(self):
        self.lock.unlink()
        self.assertFalse(control.running(self.root))
        self.assertEqual(control.read_status(self.root),{})
        (self.archive/'status.json').write_text('{partial',encoding='utf-8')
        self.assertEqual(control.read_status(self.root),{})
        self.assertEqual(control.status_lines(self.root),['Download is NOT running.'])

    def test_normal_completion_and_finished_errors_are_explicit(self):
        for state,message in (('finished_available_requests','Download complete:'),
                              ('finished_with_errors','failed requests need review')):
            with self.subTest(state=state):
                self.write_status(state)
                self.assertTrue(any(message in line for line in control.status_lines(self.root)))
                with patch.object(control,'running',return_value=True):
                    self.assertFalse(any(message in line for line in control.status_lines(self.root)))

    def test_unexpected_lock_error_is_not_reported_as_running(self):
        with patch.object(control.msvcrt,'locking',side_effect=OSError(5,'Input/output failure')):
            with self.assertRaises(OSError):control.running(self.root)

    def test_duplicate_start_does_not_launch_or_require_runtime(self):
        with self.lock.open('r+b') as owner,patch.object(control.subprocess,'Popen') as launch:
            msvcrt.locking(owner.fileno(),msvcrt.LK_NBLCK,1)
            try:self.assertIn('Already running',control.start(self.root))
            finally:
                owner.seek(0)
                msvcrt.locking(owner.fileno(),msvcrt.LK_UNLCK,1)
        launch.assert_not_called()
        self.assertFalse((self.root/'logs').exists())

    def test_start_uses_bundled_python_detached_with_persistent_logs(self):
        self.prepare_launcher()
        process=SimpleNamespace(poll=Mock(return_value=None))
        with patch.object(control,'running',side_effect=[False,False,True]), \
             patch.object(control.subprocess,'Popen',return_value=process) as launch, \
             patch.object(control.time,'sleep') as sleep:
            result=control.start(self.root)
        self.assertIn('Download started',result)
        args,kwargs=launch.call_args
        self.assertEqual(args[0],[str(self.root/'.runtime'/'pythonw.exe'),str(self.root/'backtest_download.py')])
        self.assertEqual(kwargs['cwd'],self.root)
        self.assertIs(kwargs['stdin'],subprocess.DEVNULL)
        self.assertIs(kwargs['stdout'],kwargs['stderr'])
        self.assertEqual(kwargs['creationflags'],subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertNotIn('shell',kwargs)
        self.assertTrue(kwargs['stdout'].closed)
        self.assertIn('Manual start',(self.root/'logs'/'historical_download.log').read_text(encoding='utf-8'))
        process.poll.assert_called_once()
        sleep.assert_called_once_with(.25)

    def test_missing_runtime_fails_before_launch(self):
        with patch.object(control.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'Bundled Python'):
                control.start(self.root)
        launch.assert_not_called()

    def test_launch_error_propagates_without_success_message(self):
        self.prepare_launcher()
        with patch.object(control.subprocess,'Popen',side_effect=OSError('Launch denied')):
            with self.assertRaisesRegex(OSError,'Launch denied'):control.start(self.root)

    def test_old_terminal_status_cannot_hide_a_startup_failure(self):
        self.prepare_launcher()
        for state in ('finished_available_requests','finished_with_errors','paused_low_disk'):
            for exit_code in (0,1):
                with self.subTest(state=state,exit_code=exit_code):
                    self.write_status(state)
                    process=SimpleNamespace(poll=Mock(return_value=exit_code))
                    with patch.object(control.subprocess,'Popen',return_value=process):
                        with self.assertRaisesRegex(RuntimeError,'exited during startup'):
                            control.start(self.root)

    def test_fresh_successful_completion_is_recognised(self):
        self.prepare_launcher()
        for state,expected in (('finished_available_requests','Available requests are complete'),
                               ('finished_with_errors','Some requests need review')):
            with self.subTest(state=state):
                self.write_status(state)
                def launch(*args,**kwargs):
                    self.write_status(state,updated='2026-10-27T10:01:00+05:30')
                    return SimpleNamespace(poll=Mock(return_value=0))
                with patch.object(control.subprocess,'Popen',side_effect=launch):
                    self.assertIn(expected,control.start(self.root))

    def test_fresh_status_does_not_override_a_nonzero_exit_code(self):
        self.prepare_launcher()
        def launch(*args,**kwargs):
            self.write_status('finished_available_requests')
            return SimpleNamespace(poll=Mock(return_value=1))
        with patch.object(control.subprocess,'Popen',side_effect=launch):
            with self.assertRaisesRegex(RuntimeError,'exited during startup'):
                control.start(self.root)

    def test_fresh_low_disk_status_gives_an_actionable_error(self):
        self.prepare_launcher()
        def launch(*args,**kwargs):
            self.write_status('paused_low_disk')
            return SimpleNamespace(poll=Mock(return_value=0))
        with patch.object(control.subprocess,'Popen',side_effect=launch):
            with self.assertRaisesRegex(RuntimeError,'free at least 10 GB'):
                control.start(self.root)

    def test_unconfirmed_start_times_out_without_launching_again(self):
        self.prepare_launcher()
        with patch.object(control.subprocess,'Popen',return_value=SimpleNamespace(poll=Mock(return_value=None))) as launch, \
             patch.object(control.time,'monotonic',side_effect=[0.,0.,16.]), \
             patch.object(control.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'Startup has not been confirmed'):
                control.start(self.root,timeout=15)
        launch.assert_called_once()

    def test_status_cli_never_starts_a_download_and_errors_return_nonzero(self):
        with patch('sys.argv',['download_control.py','--status']), \
             patch.object(control,'start') as start, \
             patch.object(control,'status_lines',return_value=['Download is NOT running.']), \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(control.main(),0)
        start.assert_not_called()
        self.assertIn('NOT running',output.getvalue())
        with patch('sys.argv',['download_control.py']), \
             patch.object(control,'start',side_effect=RuntimeError('Test failure')), \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(control.main(),1)
        self.assertIn('Could not start or confirm',output.getvalue())
        self.assertNotIn('Download started',output.getvalue())


if __name__=='__main__':main()
