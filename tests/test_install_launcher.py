"""Local startup tests, synthetic folders only, no real browser is opened."""
import contextlib
import io
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.request

from chemical_cabinet import launcher


class EasyLocalStartupTests(unittest.TestCase):
    def test_start_default_is_stable_absolute_home_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(Path, 'home', return_value=home), \
                 mock.patch.object(launcher, 'make_handler', return_value=object()) as handler, \
                 mock.patch.object(launcher, 'ThreadingHTTPServer') as server, \
                 mock.patch.object(launcher, 'open_browser') as browser, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                active = server.return_value.__enter__.return_value
                active.server_address = ('127.0.0.1', 12345)
                active.serve_forever.side_effect = KeyboardInterrupt
                launcher.main(['--no-browser'])
                handler.assert_called_once_with(str(home / 'chemical-cabinet-data'))
                self.assertEqual(server.call_args.args[0], ('127.0.0.1', 8080))
                browser.assert_not_called()
                self.assertIn(str(home / 'chemical-cabinet-data'), output.getvalue())
                self.assertIn('http://127.0.0.1:12345/', output.getvalue())
                self.assertIn('Ctrl+C', output.getvalue())

    def test_start_occupied_port_never_opens_browser_or_foreign_url(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with mock.patch.object(launcher.webbrowser, 'open') as browser, \
                 contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as failure:
                    launcher.main(['--data', tmp, '--port', str(port)])
            self.assertEqual(failure.exception.code, 2)
            browser.assert_not_called()
            self.assertIn('no browser was opened', errors.getvalue())

    def test_start_incomplete_restore_keeps_existing_guard_and_no_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'RESTORE_INCOMPLETE'
            marker.write_text('synthetic incomplete restore')
            with mock.patch.object(launcher.webbrowser, 'open') as browser, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    launcher.main(['--data', tmp, '--port', '0'])
            browser.assert_not_called()
            self.assertEqual(marker.read_text(), 'synthetic incomplete restore')
            self.assertFalse((Path(tmp) / 'cabinet.sqlite3').exists())

    def test_start_relative_data_and_invalid_port_fail_before_app_creation(self):
        for args in [['--data', './relative'], ['--port', '-1'], ['--port', '65536']]:
            with mock.patch.object(launcher, 'make_handler') as handler, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    launcher.main(args)
                handler.assert_not_called()

    def test_start_browser_failure_is_nonfatal_and_only_exact_url_used(self):
        url = 'http://127.0.0.1:12345/'
        for outcome in [False, OSError('synthetic unavailable browser')]:
            with mock.patch.object(launcher.webbrowser, 'open', side_effect=outcome if isinstance(outcome, Exception) else None,
                                   return_value=outcome) as browser, \
                 contextlib.redirect_stderr(io.StringIO()) as errors:
                launcher.open_browser(url)
                browser.assert_called_once_with(url, new=2)
                self.assertIn('Copy the local URL', errors.getvalue())

    def test_start_browser_thread_only_after_successful_bind(self):
        order = []
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(launcher, 'make_handler', return_value=object()), \
             mock.patch.object(launcher, 'ThreadingHTTPServer') as factory, \
             mock.patch.object(launcher.threading, 'Thread') as thread, \
             contextlib.redirect_stdout(io.StringIO()):
            server = factory.return_value.__enter__.return_value
            server.server_address = ('127.0.0.1', 4567)
            factory.side_effect = lambda *a: (order.append('bind') or factory.return_value)
            thread.return_value.start.side_effect = lambda: order.append('browser')
            server.serve_forever.side_effect = KeyboardInterrupt
            launcher.main(['--data', tmp, '--port', '0'])
            self.assertEqual(order, ['bind', 'browser'])
            target = thread.call_args.kwargs['target']
            self.assertEqual(target.args, ('http://127.0.0.1:4567/',))
            self.assertTrue(thread.call_args.kwargs['daemon'])
            factory.return_value.__exit__.assert_called_once()

    @unittest.skipUnless(os.name == 'posix', 'SIGINT shutdown checked on POSIX only')
    def test_start_real_http_and_ctrl_c_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
            probe.close()
            child = subprocess.Popen([sys.executable, '-m', 'chemical_cabinet.launcher', '--data', tmp,
                                      '--port', str(port), '--no-browser'],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 8
                while True:
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:%d/' % port, timeout=.3) as r:
                            self.assertEqual(r.status, 200)
                            self.assertIn(b'Cabinet', r.read())
                        break
                    except OSError:
                        if child.poll() is not None or time.monotonic() > deadline:
                            self.fail('local startup failed')
                        time.sleep(.05)
                child.send_signal(signal.SIGINT)
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr)
                self.assertIn('Stopped.', stdout)
                self.assertTrue((Path(tmp) / 'cabinet.sqlite3').is_file())
                self.assertNotIn('Traceback', stderr)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
