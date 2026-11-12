import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from experiment_names import ExperimentNames, experiment_path, run_bridge, validate_name
from swanlab_bridge import execute, route_for


PATH = 'researcher/training/short-run'
READ_ROUTE = '/project/researcher/training/runs/short-run'
WRITE_ROUTE = '/project/researcher/training/runs/internal-cuid'


class FakeClient:
    """Serve independent cloud snapshots and record all attempted writes."""

    def __init__(self, names=('旧名称', '新名称'), write_error=None):
        self.names = iter(names)
        self.write_error = write_error
        self.reads = []
        self.writes = []

    def request(self, method, route, **kwargs):
        if method != 'GET':
            raise AssertionError('Only metadata reads use the generic request method')
        self.reads.append((route, kwargs))
        return SimpleNamespace(data={
            'name': next(self.names),
            'cuid': 'internal-cuid',
            'slug': 'short-run',
            'description': '保留描述',
            'state': 'RUNNING',
            'config': {'learning_rate': 0.001},
        })

    def put(self, route, data, **kwargs):
        self.writes.append((route, data, kwargs))
        if self.write_error:
            raise self.write_error
        return SimpleNamespace(data=None)


def rename_payload(name='新名称', expected_name='旧名称'):
    return {'action': 'rename', 'path': PATH, 'name': name,
            'expected_name': expected_name}


class RemoteRenameTests(unittest.TestCase):
    def test_rename_resolves_internal_identifier_writes_only_name_and_reads_back(self):
        client = FakeClient()
        result = execute(rename_payload(), client)

        self.assertTrue(result['ok'])
        self.assertEqual(result['experiment']['name'], '新名称')
        self.assertEqual(result['experiment']['path'], PATH)
        self.assertEqual(client.writes, [
            (WRITE_ROUTE, {'name': '新名称'}, {'retries': 0}),
        ])
        self.assertEqual(client.reads, [
            (READ_ROUTE, {'retries': 0, 'timeout': 5, 'log_error': False}),
            (READ_ROUTE, {'retries': 0, 'timeout': 5, 'log_error': False}),
        ])

    def test_concurrent_cloud_rename_is_reported_without_overwriting_it(self):
        client = FakeClient(names=('其他人修改后的名称',))
        result = execute(rename_payload(), client)

        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'conflict')
        self.assertEqual(result['current_name'], '其他人修改后的名称')
        self.assertEqual(client.writes, [])

    def test_same_cloud_name_is_successful_without_a_write(self):
        for expected in ('新名称', '更早的名称'):
            with self.subTest(expected=expected):
                client = FakeClient(names=('新名称',))
                result = execute(rename_payload(expected_name=expected), client)
                self.assertTrue(result['ok'])
                self.assertEqual(client.writes, [])
                self.assertEqual(len(client.reads), 1)

    def test_successful_request_with_different_readback_is_not_reported_as_saved(self):
        client = FakeClient(names=('旧名称', '另一个云端名称'))
        result = execute(rename_payload(), client)

        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'unconfirmed')
        self.assertNotIn('experiment', result)
        self.assertEqual(len(client.writes), 1)

    def test_failed_write_is_not_retried_or_followed_by_a_success_readback(self):
        client = FakeClient(names=('旧名称',), write_error=RuntimeError('network failure'))
        with self.assertRaises(RuntimeError):
            execute(rename_payload(), client)
        self.assertEqual(len(client.writes), 1)
        self.assertEqual(len(client.reads), 1)

    def test_missing_internal_identifier_prevents_a_write(self):
        client = Mock()
        client.request.return_value = SimpleNamespace(data={'name': '旧名称', 'slug': 'short-run'})
        with self.assertRaises(ValueError):
            execute(rename_payload(), client)
        client.put.assert_not_called()

    def test_batch_read_keeps_other_experiments_when_one_is_unavailable(self):
        other_path = 'researcher/training/another-run'
        client = Mock()
        client.request.side_effect = [
            RuntimeError('not available'),
            SimpleNamespace(data={'name': '可以访问', 'cuid': 'other-cuid'}),
        ]
        result = execute({'action': 'read', 'paths': [PATH, other_path]}, client)

        self.assertTrue(result['ok'])
        self.assertFalse(result['experiments'][PATH]['ok'])
        self.assertEqual(result['experiments'][other_path]['name'], '可以访问')
        client.put.assert_not_called()

    def test_batch_emits_each_completed_item_before_reading_the_next(self):
        client = Mock()
        emitted = []

        def read(method, route, **kwargs):
            if route != READ_ROUTE:
                self.assertEqual(emitted[0][0], PATH)
                self.assertEqual(emitted[0][1]['name'], '先完成的实验')
                raise RuntimeError('slow request timed out')
            return SimpleNamespace(data={'name': '先完成的实验', 'cuid': 'internal-cuid'})

        client.request.side_effect = read
        execute({'action': 'read', 'paths': [PATH, 'researcher/training/slow-run']},
                client, progress=lambda path, row: emitted.append((path, row)))
        self.assertEqual(len(emitted), 2)
        self.assertIn('error', emitted[1][1])


class BridgeWatchdogTests(unittest.TestCase):
    def test_completed_name_survives_watchdog_when_next_read_stalls(self):
        slow_path = 'researcher/training/slow-run'
        names = ExperimentNames()
        names.cache[slow_path] = {'name': '已缓存名称', 'synced_at': 10, 'error': ''}
        real_run = subprocess.run

        def short_watchdog(*args, **kwargs):
            kwargs['timeout'] = 1
            return real_run(*args, **kwargs)

        with tempfile.TemporaryDirectory() as folder:
            # A real child flushes the first result, then hangs during its next read.
            script = Path(folder) / 'swanlab_bridge.py'
            script.write_text(
                'import json, sys, time\n'
                'payload = json.load(sys.stdin)\n'
                'print(json.dumps({"progress": {"path": payload["paths"][0], '
                '"name": "已从云端更新"}}, ensure_ascii=False), flush=True)\n'
                'time.sleep(10)\n', encoding='utf-8')
            with patch('experiment_names.ROOT', Path(folder)), \
                 patch.dict('os.environ', {'AUTODL_SWANLAB_PYTHON': sys.executable}), \
                 patch('experiment_names.subprocess.run', side_effect=short_watchdog):
                names.refresh([PATH, slow_path])

        self.assertEqual(names.cache[PATH]['name'], '已从云端更新')
        self.assertEqual(names.cache[PATH]['error'], '')
        self.assertEqual(names.cache[slow_path]['name'], '已缓存名称')
        self.assertEqual(names.cache[slow_path]['synced_at'], 10)
        self.assertIn('超时', names.cache[slow_path]['error'])

    def test_progress_lines_and_final_result_are_both_accepted(self):
        row = {'path': PATH, 'name': '已更新'}
        final = {'ok': True, 'experiments': {PATH: row}}
        output = json.dumps({'progress': row}) + '\n' + json.dumps(final) + '\n'
        completed = subprocess.CompletedProcess([], 0, stdout=output, stderr='')
        with patch('experiment_names.subprocess.run', return_value=completed), \
             patch.dict('os.environ', {'AUTODL_SWANLAB_PYTHON': sys.executable}):
            result = run_bridge({'action': 'read', 'paths': [PATH]})
        self.assertEqual(result, final)


class NameCacheTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Mock()
        self.names = ExperimentNames(bridge=self.bridge)
        self.names.cache[PATH] = {'name': '旧名称', 'synced_at': 10, 'error': ''}

    def test_verified_rename_updates_cache_with_cloud_name(self):
        self.bridge.return_value = {
            'ok': True, 'experiment': {'path': PATH, 'name': '新名称', 'cuid': 'internal-cuid'},
        }
        with patch('experiment_names.time.time', return_value=20):
            result = self.names.rename(PATH, '  新名称  ', '旧名称')

        self.assertTrue(result['ok'])
        self.bridge.assert_called_once_with(rename_payload())
        self.assertEqual(self.names.cache[PATH], {'name': '新名称', 'synced_at': 20, 'error': ''})

    def test_failed_or_unconfirmed_rename_preserves_last_verified_cache(self):
        for code in ('conflict', 'remote_error', 'timeout', 'unconfirmed'):
            with self.subTest(code=code):
                before = copy.deepcopy(self.names.cache)
                self.names.wake.clear()
                self.bridge.return_value = {'ok': False, 'code': code, 'error': '未保存'}
                result = self.names.rename(PATH, '新名称', '旧名称')

                self.assertFalse(result['ok'])
                self.assertEqual(self.names.cache, before)
                self.assertTrue(self.names.wake.is_set())

    def test_external_cloud_rename_replaces_stale_log_name_without_mutating_log(self):
        self.bridge.return_value = {'ok': True, 'experiments': {
            PATH: {'name': '网页修改后的名称', 'cuid': 'internal-cuid'},
        }}
        original = {'workspace': 'researcher', 'project': 'training',
                    'run_id': 'short-run', 'name': '训练日志中的名称', 'extra': {'pid': 42}}
        before = copy.deepcopy(original)
        with patch('experiment_names.time.time', return_value=30):
            self.names.refresh([PATH, PATH, ''])
        displayed = self.names.decorate(original)

        self.bridge.assert_called_once_with({'action': 'read', 'paths': [PATH]})
        self.assertEqual(displayed['name'], '网页修改后的名称')
        self.assertEqual(displayed['name_source'], 'cloud')
        self.assertEqual(displayed['name_synced_at'], 30)
        self.assertEqual(displayed['name_sync_error'], '')
        self.assertEqual(original, before)

    def test_refresh_failure_keeps_verified_name_and_marks_it_as_cached(self):
        for response in (
            {'ok': False, 'error': '暂时离线'},
            {'ok': True, 'experiments': {PATH: {'error': '暂时离线'}}},
        ):
            with self.subTest(response=response):
                self.bridge.return_value = response
                self.names.refresh([PATH])
                displayed = self.names.decorate({'workspace': 'researcher', 'project': 'training',
                                                 'run_id': 'short-run', 'name': '日志名称'})
                self.assertEqual(displayed['name'], '旧名称')
                self.assertEqual(displayed['name_synced_at'], 10)
                self.assertEqual(displayed['name_source'], 'cache')
                self.assertEqual(displayed['name_sync_error'], '暂时离线')

    def test_no_cloud_name_uses_log_name(self):
        self.names.cache.clear()
        displayed = self.names.decorate({'workspace': 'researcher', 'project': 'training',
                                         'run_id': 'short-run', 'name': '日志名称'})
        self.assertEqual(displayed['name'], '日志名称')
        self.assertEqual(displayed['name_source'], 'log')
        self.assertIsNone(displayed['name_synced_at'])

    def test_invalid_names_and_missing_previous_name_do_not_call_bridge(self):
        invalid = [None, 123, '', '   ', '名' * 101, '换\n行', '制\x00表', '结尾\x7f']
        for name in invalid:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.names.rename(PATH, name, '旧名称')
        for previous in (None, '', 123):
            with self.subTest(previous=previous), self.assertRaises(ValueError):
                self.names.rename(PATH, '新名称', previous)
        self.bridge.assert_not_called()


class ValidationTests(unittest.TestCase):
    def test_unicode_names_and_maximum_length_are_accepted(self):
        self.assertEqual(validate_name('  实验一 / 学习率 0.001  '), '实验一 / 学习率 0.001')
        self.assertEqual(validate_name('名' * 100), '名' * 100)

    def test_routes_encode_each_path_component(self):
        self.assertEqual(route_for('researcher/训练 项目/run#1'),
                         '/project/researcher/%E8%AE%AD%E7%BB%83%20%E9%A1%B9%E7%9B%AE/runs/run%231')

    def test_invalid_remote_paths_are_rejected(self):
        for path in (None, 7, '', 'one/two', 'one/two/three/four',
                     '/project/run', 'owner/../run', 'owner/project/run\n',
                     'owner/project/' + 'r' * 513):
            with self.subTest(path=path), self.assertRaises(ValueError):
                route_for(path)

    def test_invalid_log_identity_cannot_be_used_for_cloud_sync(self):
        valid = {'workspace': 'researcher', 'project': 'training', 'run_id': 'short-run'}
        self.assertEqual(experiment_path(valid), PATH)
        for key in valid:
            for value in (None, '', '../other', '..', '\x00', 'r' * 513):
                with self.subTest(key=key, value=value):
                    self.assertEqual(experiment_path(dict(valid, **{key: value})), '')


if __name__ == '__main__':
    unittest.main()
