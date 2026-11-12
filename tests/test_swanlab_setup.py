import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import swanlab_config as config
from experiment_names import run_bridge


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'AUTODL_CONFIG_DIR': self.temp.name,
                                          'AUTODL_SWANLAB_PYTHON': sys.executable,
                                          'SWANLAB_API_KEY': ''})
        self.env.start()
        config._status_cache.clear()
        config._runtime_cache.clear()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_verified_key_is_private_and_never_returned(self):
        result = {'ok': True, 'username': 'example-user'}
        with patch('experiment_names.run_bridge', return_value=result) as bridge:
            status = config.configure({'api_key': 'example-secret', 'python': ''})
        credential = self.root / 'swanlab-credentials.json'
        self.assertEqual(json.loads(credential.read_text()), {'api_key': 'example-secret'})
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / 'swanlab-settings.json').stat().st_mode), 0o600)
        self.assertNotIn('example-secret', json.dumps(status))
        self.assertEqual(status['username'], 'example-user')
        self.assertEqual(status['credential_source'], 'local')
        self.assertTrue(status['configured'])
        self.assertEqual(bridge.call_args.kwargs['runtime']['api_key'], 'example-secret')

    def test_failed_login_preserves_saved_credentials_and_python(self):
        config._write(self.root / 'swanlab-credentials.json', {'api_key': 'existing-key'})
        config._write(self.root / 'swanlab-settings.json', {'python': sys.executable})
        with patch('experiment_names.run_bridge', return_value={'ok': False, 'code': 'not_logged_in'}):
            status = config.configure({'api_key': 'invalid-key', 'python': ''})
        self.assertFalse(status['ok'])
        self.assertEqual(json.loads((self.root / 'swanlab-credentials.json').read_text()), {'api_key': 'existing-key'})
        self.assertEqual(json.loads((self.root / 'swanlab-settings.json').read_text()), {'python': sys.executable})

    def test_clearing_only_removes_app_credentials_and_uses_system_account(self):
        credentials = self.root / 'swanlab-credentials.json'
        config._write(credentials, {'api_key': 'local-key'})
        unrelated = self.root / '.netrc'
        unrelated.write_text('leave this file alone')
        with patch('experiment_names.run_bridge', return_value={'ok': True, 'username': 'system-user'}) as bridge:
            result = config.clear_credentials()
        self.assertFalse(credentials.exists())
        self.assertEqual(unrelated.read_text(), 'leave this file alone')
        self.assertTrue(result['cleared'])
        self.assertFalse(result['configured'])
        self.assertEqual(result['credential_source'], 'system')
        self.assertIsNone(bridge.call_args.kwargs['runtime']['api_key'])

    def test_status_uses_cache_and_refresh_revalidates(self):
        with patch('experiment_names.run_bridge', return_value={'ok': True, 'username': 'user'}) as bridge:
            config.setup_status()
            config.setup_status()
            self.assertEqual(bridge.call_count, 1)
            config.setup_status(refresh=True)
            self.assertEqual(bridge.call_count, 2)

    def test_sdk_missing_has_actionable_status(self):
        with patch('experiment_names.run_bridge', return_value={'ok': False, 'code': 'sdk_missing'}):
            result = config.setup_status()
        self.assertEqual(result['state'], 'sdk_missing')
        self.assertIn('install.py', result['message'])
        self.assertNotIn('api_key', result)

    def test_password_never_enters_command_arguments_or_child_environment(self):
        completed = subprocess.CompletedProcess([], 0, stdout='{"ok":true,"username":"user"}\n', stderr='')
        runtime = {'python': sys.executable, 'api_key': 'example-secret'}
        with patch('experiment_names.subprocess.run', return_value=completed) as run:
            result = run_bridge({'action': 'status'}, runtime=runtime)
        self.assertTrue(result['ok'])
        self.assertNotIn('example-secret', repr(run.call_args.args))
        self.assertNotIn('env', run.call_args.kwargs)
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['api_key'], 'example-secret')

    def test_invalid_configuration_does_not_contact_cloud(self):
        with patch('experiment_names.run_bridge') as bridge:
            for data in ({'host': 'https://unknown.invalid'}, {'api_key': 'line\nbreak'},
                         {'api_key': 123}, {'python': '/missing/python'}, {}, []):
                with self.subTest(data=data), self.assertRaises(ValueError):
                    config.configure(data)
            bridge.assert_not_called()

    def test_corrupt_credentials_can_be_replaced_after_successful_login(self):
        (self.root / 'swanlab-credentials.json').write_text('broken')
        with patch('experiment_names.run_bridge', return_value={'ok': True, 'username': 'user'}):
            result = config.configure({'python': '', 'api_key': 'valid-key'})
        self.assertTrue(result['ok'])
        self.assertEqual(json.loads((self.root / 'swanlab-credentials.json').read_text())['api_key'], 'valid-key')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        config._runtime_cache.clear()

    def test_environment_python_takes_priority_over_saved_setting(self):
        with patch.dict(os.environ, {'AUTODL_SWANLAB_PYTHON': sys.executable}):
            self.assertEqual(config.resolve_python('/missing/saved-python'), str(Path(sys.executable).absolute()))

    def test_discovery_prefers_project_environment_with_sdk(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.object(config, 'ROOT', root), patch.dict(os.environ, {'AUTODL_SWANLAB_PYTHON': ''}), \
                 patch.object(config, '_has_sdk', return_value=True):
                self.assertEqual(config.resolve_python(''), str(executable))

    def test_launcher_shebang_supplies_a_portable_interpreter(self):
        with tempfile.TemporaryDirectory() as folder:
            launcher = Path(folder) / 'swanlab'
            launcher.write_text('#!' + sys.executable + '\n')
            with patch.object(config.shutil, 'which', return_value=str(launcher)):
                self.assertEqual(config._launcher_python(), sys.executable)


if __name__ == '__main__':
    unittest.main()
