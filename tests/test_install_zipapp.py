"""Portable packaging checks; invented demo data only, no external services."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile

from chemical_cabinet import __version__

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cabinet_build_zipapp', ROOT / 'tools/build_zipapp.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class PortableInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cabinet-install-test-')
        self.root = Path(self.tmp.name)
        self.code = self.root / 'source with spaces'
        self.code.mkdir()
        shutil.copytree(ROOT / 'chemical_cabinet', self.code / 'chemical_cabinet', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT / 'LICENSE', self.code / 'LICENSE')
        self.artifact = self.root / 'portable copy' / 'chemical-cabinet.pyz'
        self.cwd = self.root / 'different working folder'
        self.cwd.mkdir()
        self.data = self.root / 'inventory outside code'

    def tearDown(self):
        self.tmp.cleanup()

    def run_archive(self, *args, input=None):
        return subprocess.run([sys.executable, str(self.artifact)] + list(args), cwd=self.cwd,
                              input=input, text=True, capture_output=True, timeout=12)

    def test_install_archive_contains_code_license_and_no_inventory(self):
        (self.code / 'data').mkdir()
        (self.code / 'data' / 'cabinet.sqlite3').write_bytes(b'not real data')
        (self.code / 'chemical_cabinet' / 'ignored.pdf').write_bytes(b'not an SDS')
        builder.build(self.artifact, self.code)
        with zipfile.ZipFile(self.artifact) as archive:
            names = archive.namelist()
            self.assertIn('LICENSE', names)
            self.assertIn('chemical_cabinet/backup.py', names)
            self.assertTrue(all(n.endswith('/') or n.endswith('.py') or n == 'LICENSE' for n in names))
            self.assertFalse(any('data/' in n or '.pdf' in n or '__pycache__' in n for n in names))
        # No source checkout is needed at runtime.
        shutil.rmtree(self.code)
        self.assertEqual(self.run_archive('--version').stdout.strip(), __version__)
        for command in ['start', 'web', 'mcp', 'backup']:
            result = self.run_archive(command, '--help')
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_install_requires_absolute_data_before_any_creation(self):
        builder.build(self.artifact, self.code)
        for command in ['web', 'mcp']:
            for args in [[], ['--data', 'relative'], ['--data=relative'], ['--data', str(self.data), '--data', 'relative']]:
                result = self.run_archive(command, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('absolute folder', result.stderr)
                self.assertFalse(self.data.exists())
        self.assertNotEqual(self.run_archive('not-a-command').returncode, 0)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_install_real_loopback_start_and_code_only_update_preserve_data(self):
        builder.build(self.artifact, self.code)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen([sys.executable, str(self.artifact), 'web', '--data', str(self.data), '--port', str(port)],
                                   cwd=self.cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while True:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%d/' % port, timeout=0.5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(b'Cabinet', response.read())
                    break
                except (OSError, urllib.error.URLError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        self.fail('Portable local web startup failed')
                    time.sleep(0.05)
        finally:
            process.terminate()
            process.communicate(timeout=5)
        database = self.data / 'cabinet.sqlite3'
        self.assertTrue(database.is_file())
        # Add a synthetic attachment sentinel: the build/update must never read or alter data.
        sentinel = self.data / 'demo-attachment.txt'
        sentinel.write_text('invented demo attachment', encoding='utf-8')
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [database, sentinel]}
        source = self.code / 'chemical_cabinet' / '__init__.py'
        source.write_text('__version__ = "0.1.1+local-demo"\n', encoding='utf-8')
        builder.build(self.artifact, self.code, overwrite=True)
        self.assertEqual(self.run_archive('--version').stdout.strip(), '0.1.1+local-demo')
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [database, sentinel]}
        self.assertEqual(before, after)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_install_mcp_uses_same_external_folder_without_source_checkout(self):
        builder.build(self.artifact, self.code)
        shutil.rmtree(self.code)
        self.data.mkdir()
        with sqlite3.connect(str(self.data / 'cabinet.sqlite3')) as connection:
            connection.execute('CREATE TABLE demo_setup (id INTEGER)')
        # Initialization alone is a protocol smoke check, not tool/restore validation.
        messages = json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}}) + '\n'
        result = self.run_archive('mcp', '--data=' + str(self.data), input=messages)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = json.loads(result.stdout)
        self.assertEqual(reply['id'], 1)
        self.assertEqual(reply['result']['serverInfo']['version'], __version__)

    def test_install_abbreviated_data_cannot_override_absolute_path(self):
        builder.build(self.artifact, self.code)
        for command in ['web', 'mcp']:
            for alias in ['--d', '--da', '--dat', '--dat=relative']:
                result = self.run_archive(command, '--data', str(self.data), alias, 'relative')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('full --data', result.stderr)
                self.assertFalse(self.data.exists())
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_install_easy_start_real_archive_from_foreign_directory(self):
        builder.build(self.artifact, self.code)
        shutil.rmtree(self.code)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        child = subprocess.Popen([sys.executable, str(self.artifact), 'start', '--data', str(self.data),
                                  '--port', str(port), '--no-browser'], cwd=self.cwd,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 8
            while True:
                try:
                    with urllib.request.urlopen('http://127.0.0.1:%d/' % port, timeout=.3) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(b'Cabinet', response.read())
                    break
                except OSError:
                    if child.poll() is not None or time.monotonic() > deadline:
                        self.fail('Portable easy start failed')
                    time.sleep(.05)
        finally:
            child.terminate()
            stdout, stderr = child.communicate(timeout=5)
        self.assertIn(str(self.data), stdout)
        self.assertIn('http://127.0.0.1:%d/' % port, stdout)
        self.assertNotIn('Traceback', stderr)
        self.assertTrue((self.data / 'cabinet.sqlite3').is_file())
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_install_output_and_source_guards_fail_without_replacement(self):
        builder.build(self.artifact, self.code)
        old = self.artifact.read_bytes()
        with self.assertRaises(ValueError):
            builder.build(self.artifact, self.code)
        self.assertEqual(self.artifact.read_bytes(), old)
        with self.assertRaises(ValueError):
            builder.build(self.code / 'chemical_cabinet' / 'bad.pyz', self.code)
        with self.assertRaises(ValueError):
            builder.build(self.root / 'wrong.py', self.code)
        (self.code / 'chemical_cabinet' / 'db.py').unlink()
        with self.assertRaises(ValueError):
            builder.build(self.artifact, self.code, overwrite=True)
        self.assertEqual(self.artifact.read_bytes(), old)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlinks unavailable')
    def test_install_symlink_package_member_and_output_rejected(self):
        other = self.root / 'external.py'
        other.write_text('secret = "synthetic demo"\n')
        link = self.code / 'chemical_cabinet' / 'unexpected.py'
        try:
            link.symlink_to(other)
        except OSError:
            self.skipTest('symlink creation not permitted')
        with self.assertRaises(ValueError):
            builder.build(self.artifact, self.code)
        link.unlink()
        self.artifact.parent.mkdir()
        self.artifact.symlink_to(other)
        with self.assertRaises(ValueError):
            builder.build(self.artifact, self.code, overwrite=True)
        self.assertEqual(other.read_text(), 'secret = "synthetic demo"\n')


if __name__ == '__main__':
    unittest.main()
