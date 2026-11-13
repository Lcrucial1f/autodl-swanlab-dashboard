import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import remote_probe as probe


class OpenLogDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cwd = self.root / 'training'
        self.cwd.mkdir()
        self.now = time.time()
        self.main = self.member(42)
        self.logger = self.member(43, ppid=42)

    def member(self, pid, ppid=1):
        return {'pid': pid, 'ppid': ppid, 'start': self.now - 120,
                'cwd': str(self.cwd), 'exe': '/env/bin/python',
                'argv': ['/env/bin/python', 'train.py']}

    def run_folder(self, parent=None, rid='sample-run', pid=42,
                   hostname='sample-container', ago=30):
        parent = parent or self.root / 'storage' / 'custom' / 'project' / 'logs'
        stamp = datetime.datetime.fromtimestamp(self.now - ago).strftime('%Y%m%d_%H%M%S')
        folder = parent / ('run-' + stamp + '-' + rid)
        (folder / 'files').mkdir(parents=True)
        (folder / 'debug').mkdir()
        (folder / 'files' / 'swanlab-metadata.json').write_text(json.dumps({
            'runtime': {'pid': pid, 'hostname': hostname, 'cwd': str(self.cwd)},
        }))
        (folder / ('run-' + rid + '.swanlab')).write_bytes(b'fake record')
        (folder / 'debug' / 'core.log').write_text('sample log')
        return folder

    @contextlib.contextmanager
    def open_fds(self, targets, inaccessible=None):
        """Only /proc links are simulated; run directories and symlinks are real."""
        inaccessible = inaccessible or {}
        original_iterdir, original_readlink = Path.iterdir, os.readlink
        links = {str(Path('/proc') / str(pid) / 'fd' / str(fd)): value
                 for pid, descriptors in targets.items() for fd, value in descriptors.items()}

        def iterdir(path):
            match = re.fullmatch(r'/proc/(\d+)/fd', str(path))
            if not match:
                return original_iterdir(path)
            pid = int(match[1])
            if pid in inaccessible:
                raise inaccessible[pid]
            return iter(Path('/proc') / str(pid) / 'fd' / str(fd)
                        for fd in targets.get(pid, {}))

        def readlink(path, *args, **kwargs):
            key = os.fsdecode(path)
            if not key.startswith('/proc/'):
                return original_readlink(path, *args, **kwargs)
            if key not in links:
                raise FileNotFoundError(key)
            value = links[key]
            if isinstance(value, Exception):
                raise value
            return str(value)

        with patch.object(Path, 'iterdir', iterdir), patch.object(os, 'readlink', readlink):
            yield

    def header(self, command, **kwargs):
        rid = Path(command[-1]).stem[len('run-'):]
        record = {'project': 'sample-project', 'workspace': 'sample-owner',
                  'name': 'sample experiment', 'run_id': rid}
        return subprocess.CompletedProcess(command, 0,
                                           '__SWAN_HEADER__' + json.dumps(record), '')

    def test_deep_custom_log_opened_by_logger_child_is_associated(self):
        folder = self.run_folder()
        with self.open_fds({43: {9: folder / 'debug' / 'core.log'}}), \
             patch.object(probe.subprocess, 'run', side_effect=self.header):
            self.assertEqual(probe.open_run_dirs([self.main, self.logger]), {folder.resolve()})
            experiments = probe.experiment_for([self.main, self.logger], 'sample-container')
        self.assertEqual(len(experiments), 1)
        self.assertEqual(experiments[0]['run_id'], 'sample-run')
        self.assertEqual(experiments[0]['project'], 'sample-project')
        self.assertTrue(experiments[0]['url'].endswith('/runs/sample-run/chart'))

    def test_open_binary_bypasses_recent_thirty_directory_limit(self):
        log_dir = self.cwd / 'swanlog'
        active = self.run_folder(log_dir, ago=100)
        os.utime(active, (self.now - 100, self.now - 100))
        for index in range(31):
            other = self.run_folder(log_dir, rid='other-%02d' % index, pid=999)
            os.utime(other, (self.now + index, self.now + index))
        with self.open_fds({43: {8: active / 'run-sample-run.swanlab'}}), \
             patch.object(probe.subprocess, 'run', side_effect=self.header):
            experiments = probe.experiment_for([self.main, self.logger], 'sample-container')
        self.assertEqual([item['run_id'] for item in experiments], ['sample-run'])

    def test_symlink_and_direct_discovery_of_same_run_are_deduplicated(self):
        log_dir = self.cwd / 'swanlog'
        folder = self.run_folder(log_dir)
        alias = self.root / 'logs-alias'
        alias.symlink_to(log_dir, target_is_directory=True)
        alias_folder = alias / folder.name
        with self.open_fds({43: {8: alias_folder / 'run-sample-run.swanlab',
                                9: folder / 'debug' / 'core.log'}}), \
             patch.object(probe, 'candidate_dirs', return_value={log_dir, alias}), \
             patch.object(probe.subprocess, 'run', side_effect=self.header) as read_header:
            experiments = probe.experiment_for([self.main, self.logger], 'sample-container')
        self.assertEqual(len(experiments), 1)
        self.assertEqual(Path(experiments[0]['log_dir']), folder.resolve())
        self.assertEqual(read_header.call_count, 1)

    def test_open_descriptor_does_not_bypass_identity_or_start_time_checks(self):
        for index, invalid in enumerate(({'pid': 999}, {'hostname': 'different-container'}, {'ago': 1000})):
            with self.subTest(invalid=invalid):
                folder = self.run_folder(self.root / ('invalid-%d' % index), **invalid)
                with self.open_fds({43: {9: folder / 'debug' / 'core.log'}}), \
                     patch.object(probe.subprocess, 'run') as read_header:
                    self.assertEqual(probe.experiment_for([self.main, self.logger], 'sample-container'), [])
                read_header.assert_not_called()

    def test_unrelated_process_open_files_are_not_inspected(self):
        folder = self.run_folder()
        with self.open_fds({99: {8: folder / 'run-sample-run.swanlab'}}), \
             patch.object(probe.subprocess, 'run') as read_header:
            self.assertEqual(probe.experiment_for([self.main, self.logger], 'sample-container'), [])
        read_header.assert_not_called()

    def test_disappearing_descriptors_and_denied_processes_are_ignored_without_walk(self):
        folder = self.run_folder()
        denied = self.member(44)
        exited = self.member(45)
        with self.open_fds({43: {7: FileNotFoundError('process exited'),
                                8: PermissionError('descriptor denied'),
                                9: 'socket:[123]',
                                10: folder / 'run-sample-run.swanlab'}},
                           {44: PermissionError('process denied'), 45: FileNotFoundError('process exited')}), \
             patch.object(Path, 'rglob', side_effect=AssertionError('must not recurse')), \
             patch.object(os, 'walk', side_effect=AssertionError('must not recurse')):
            self.assertEqual(probe.open_run_dirs([self.main, denied, exited, self.logger]),
                             {folder.resolve()})

    def test_unrelated_open_files_are_not_treated_as_run_directories(self):
        checkpoint = self.root / 'checkpoints' / 'model.bin'
        checkpoint.parent.mkdir()
        checkpoint.write_bytes(b'example')
        log = self.root / 'training.log'
        log.write_text('example')
        with self.open_fds({43: {8: checkpoint, 9: log, 10: 'pipe:[123]'}}):
            self.assertEqual(probe.open_run_dirs([self.main, self.logger]), set())


if __name__ == '__main__':
    unittest.main()
