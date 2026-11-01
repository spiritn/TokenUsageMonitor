import importlib.util
import json
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cli
import runtime

spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'TOKEN_MONITOR_DATA_DIR': str(self.root / 'private data')})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def test_install_and_upgrade_preserve_user_history(self):
        data = runtime.prepare_data(); history = data / 'usage.db'; history.write_bytes(b'private-history')
        destination = self.root / 'program with spaces'; commands = self.root / 'bin with spaces'
        for _ in range(2): installer.install(destination, commands, web_only=True, no_start=True)
        self.assertEqual(history.read_bytes(), b'private-history')
        self.assertFalse((destination / 'usage.db').exists())
        self.assertFalse((destination / 'config.json').exists())
        self.assertTrue((destination / 'web/index.html').exists())
        self.assertIn(runtime.APP_ID, (commands / 'token-monitor').read_text())

    def test_install_refuses_unowned_directory(self):
        destination = self.root / 'existing'; destination.mkdir()
        sentinel = destination / 'keep'; sentinel.write_text('user-file')
        with self.assertRaises(ValueError): installer.install(destination, self.root / 'bin', True, True)
        self.assertEqual(sentinel.read_text(), 'user-file')

    def test_install_refuses_existing_other_command(self):
        commands = self.root / 'bin'; commands.mkdir(); command = commands / 'token-monitor'; command.write_text('other-program')
        with self.assertRaises(ValueError): installer.install(self.root / 'app', commands, True, True)
        self.assertEqual(command.read_text(), 'other-program')

    def test_service_configuration_uses_dynamic_paths_and_private_permissions(self):
        settings = dict(port=18789, timezone='Asia/Shanghai')
        specs = cli.service_plists(settings)
        self.assertEqual(len(specs), 2)
        for label, spec in specs.items():
            plistlib.dumps(spec)
            self.assertTrue(label.startswith(runtime.APP_ID))
            self.assertEqual(spec['EnvironmentVariables']['TOKEN_MONITOR_DATA_DIR'], str(runtime.data_dir()))
            self.assertEqual(spec['Umask'], 0o077)
        self.assertEqual(len(cli.service_plists(settings, web_only=True)), 1)

    def test_configuration_does_not_require_credentials(self):
        self.assertEqual(runtime.config(), dict(port=8787, timezone='Asia/Shanghai'))
        runtime.save_config(dict(port=18789, timezone='UTC'))
        self.assertEqual(runtime.config()['timezone'], 'UTC')
        with self.assertRaises(ValueError):
            runtime.save_config(dict(port=80, timezone='UTC')); runtime.config()

    def test_uninstall_without_marker_never_deletes_source(self):
        source = self.root / 'source'; source.mkdir(); keep = source / 'keep'; keep.write_text('keep')
        with patch.object(cli, 'BASE', source), patch.object(cli, 'stop') as stop:
            cli.uninstall(); stop.assert_called_once_with(remove=True)
        self.assertEqual(keep.read_text(), 'keep')
