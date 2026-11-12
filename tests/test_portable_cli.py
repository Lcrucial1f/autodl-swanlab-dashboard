import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('test_' + name, ROOT / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class PortableCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve() / 'profile'
        with patch.dict(os.environ, {'AUTODL_CONFIG_DIR': str(self.base)}):
            self.cli = module('autodl')

    def tearDown(self):
        self.temporary.cleanup()

    def test_clean_profile_needs_no_user_files(self):
        self.assertEqual(self.cli.load(), {'next_id': 1, 'instances': []})
        self.assertEqual(self.cli.KEY, self.base / 'ssh/id_autodl_ed25519')
        self.assertEqual(self.cli.KNOWN, self.base / 'ssh/known_hosts')
        self.assertFalse(self.base.exists())

    def test_add_remove_keeps_stable_ids_and_private_config(self):
        first = self.cli.create_instance('ssh -p 2201 root@connect.region.example', region='内蒙区')
        second = self.cli.create_instance('ssh -p 2202 ubuntu@example.test', name='training')
        self.assertEqual(first['name'], 'instance-2201')
        self.assertEqual(self.cli.remove_instance(first['id']), first)
        third = self.cli.create_instance('ssh -p 2203 root@example.test')
        self.assertEqual([i['id'] for i in self.cli.load()['instances']], [second['id'], third['id']])
        self.assertEqual(third['id'], 3)
        self.assertEqual(stat.S_IMODE(self.cli.REGISTRY.stat().st_mode), 0o600)
        self.assertNotIn('password', self.cli.REGISTRY.read_text())
        self.assertEqual(stat.S_IMODE(self.cli.SSH_CONFIG.stat().st_mode), 0o600)

    def test_concurrent_writers_do_not_lose_instances(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            items = list(executor.map(lambda n: self.cli.create_instance(f'ssh -p {2200+n} root@example.test'), range(12)))
        self.assertEqual(len(self.cli.load()['instances']), 12)
        self.assertEqual(sorted(i['id'] for i in items), list(range(1,13)))

    def test_separate_processes_share_registry_lock(self):
        environment = dict(os.environ, AUTODL_CONFIG_DIR=str(self.base))
        processes = [subprocess.Popen([sys.executable, '-B', '-c',
                     'import autodl,sys; autodl.create_instance("ssh -p " + sys.argv[1] + " root@example.test")', str(2300+n)],
                     cwd=ROOT,env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for n in range(4)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode,0,stderr)
        self.assertEqual(len(self.cli.load()['instances']),4)
        self.assertEqual(self.cli.load()['next_id'],5)

    def test_rejected_input_does_not_modify_registry(self):
        self.cli.create_instance('ssh root@example.test', name='first')
        before = self.cli.REGISTRY.read_bytes()
        calls = [
            ('ssh root@example.test', {}),
            ('ssh root@other.example', {'name':'first'}),
            ('ssh -o ProxyCommand=bad root@other.example', {}),
            ('ssh root@other.example secret', {}),
            ('ssh root@other.example', {'name':'bad\nHost *'}),
            ('ssh root@other.example', {'region':'bad\nHost *'}),
            ('ssh root@other.example', {'identity_file':'missing-key'}),
            ('ssh root@other.example', {'name':None}),
        ]
        for command, options in calls:
            with self.subTest(command=command, options=options), self.assertRaises(ValueError):
                self.cli.create_instance(command, **options)
            self.assertEqual(self.cli.REGISTRY.read_bytes(), before)

    def test_existing_identity_is_used_without_copying_private_key(self):
        identity = Path(self.temporary.name).resolve() / 'my key'
        identity.write_text('example fixture key')
        item = self.cli.create_instance('ssh root@example.test', identity_file=str(identity))
        args = self.cli.ssh_args(item)
        self.assertEqual(args[args.index('-i')+1], str(identity))
        self.assertIn('IdentityFile "'+str(identity)+'"', self.cli.SSH_CONFIG.read_text())
        self.assertFalse(self.cli.KEY.exists())
        self.assertEqual(self.cli.public_key_path(Path('key.pem')),Path('key.pem.pub'))

    def test_public_key_generation_is_idempotent_and_local(self):
        def generate(args, **kwargs):
            key = Path(args[args.index('-f')+1])
            key.write_text('fixture private')
            key.with_suffix('.pub').write_text('ssh-ed25519 fixture public')
            return subprocess.CompletedProcess(args, 0)
        with patch.object(self.cli.subprocess, 'run', side_effect=generate) as process:
            self.assertEqual(self.cli.ensure_key(), 'ssh-ed25519 fixture public')
            self.cli.ensure_key()
        self.assertEqual(process.call_count, 1)

    def test_password_only_travels_in_stdin(self):
        item = self.cli.create_instance('ssh root@example.test')
        self.cli.KEY.write_text('fixture private')
        self.cli.KEY.with_suffix('.pub').write_text('ssh-ed25519 fixture public')
        replies = [subprocess.CompletedProcess([],255,stdout='',stderr='Permission denied'),
                   subprocess.CompletedProcess([],0,stdout='',stderr=''),
                   subprocess.CompletedProcess([],0,stdout='',stderr='')]
        with patch.object(self.cli.shutil, 'which', side_effect=lambda name: '/bin/'+name), \
             patch.object(self.cli.subprocess,'run',side_effect=replies) as process:
            self.assertTrue(self.cli.install_key(item,password='example secret'))
        install = process.call_args_list[1]
        self.assertEqual(install.kwargs['input'], 'example secret\n')
        self.assertNotIn('example secret', str(install.args))
        self.assertNotIn('env', install.kwargs)
        self.assertIn(str(ROOT / 'install-key.exp'), install.args[0])
        self.assertNotIn('example secret', self.cli.REGISTRY.read_text())

    def test_host_key_mismatch_does_not_send_password(self):
        item = self.cli.create_instance('ssh root@example.test')
        self.cli.KEY.write_text('fixture private')
        self.cli.KEY.with_suffix('.pub').write_text('ssh-ed25519 fixture public')
        reply = subprocess.CompletedProcess([],255,stdout='',stderr='REMOTE HOST IDENTIFICATION HAS CHANGED')
        with patch.object(self.cli.subprocess,'run',return_value=reply) as process:
            with self.assertRaisesRegex(ValueError,'指纹'):
                self.cli.install_key(item,password='example secret')
            process.assert_called_once()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.installer = module('install')

    def test_launcher_is_runnable_in_a_clean_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self.installer.install(Path(temp) / 'bin with spaces',skip_deps=True)
            environment = dict(os.environ, AUTODL_CONFIG_DIR=str(Path(temp) / 'new profile'))
            result = subprocess.run([str(target),'config'],env=environment,text=True,capture_output=True,check=True)
            config = json.loads(result.stdout)
            self.assertEqual(config['config_dir'], str(Path(environment['AUTODL_CONFIG_DIR']).resolve()))
            self.assertEqual(config['project_dir'], str(ROOT))
            self.assertFalse((Path(environment['AUTODL_CONFIG_DIR'])/'instances.json').exists())
            self.assertEqual(self.installer.install(target.parent,skip_deps=True),target)

    def test_unrecognized_existing_command_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'autodl'
            target.write_text('other application')
            with self.assertRaisesRegex(ValueError,'未覆盖'):
                self.installer.install(temp,skip_deps=True)
            self.assertEqual(target.read_text(),'other application')


if __name__ == '__main__':
    unittest.main()
