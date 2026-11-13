from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from experiment_history import ExperimentHistory


PATH = 'researcher/training/first-run'


def samples(path=PATH, name='初始实验', machine='machine-a', machine_name='内蒙训练机', region='内蒙区'):
    workspace, project, run_id = path.split('/')
    return [{'id': machine, 'display_name': machine_name, 'region': region, 'tasks': [
        {'experiments': [{'workspace': workspace, 'project': project, 'run_id': run_id, 'name': name}]},
    ]}]


class ExperimentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = ExperimentHistory(':memory:')
        self.addCleanup(self.history.close)

    def test_observation_is_idempotent_and_does_not_establish_completion(self):
        self.history.observe(samples())
        revision = self.history.revision
        self.history.observe(samples())
        self.history.observe([])
        self.history.observe([{'id': 'machine-a', 'status': 'offline', 'tasks': []}])
        detail = self.history.detail(PATH)
        self.assertEqual(detail['name'], '初始实验')
        self.assertEqual(detail['original_name'], '初始实验')
        self.assertEqual(detail['name_source'], 'log')
        self.assertEqual(detail['state'], 'UNKNOWN')
        self.assertEqual(detail['history_count'], 0)
        self.assertEqual(detail['instances'], [{'id': 'machine-a', 'name': '内蒙训练机', 'region': '内蒙区'}])
        self.assertEqual(self.history.search()['total'], 0)
        self.assertEqual(self.history.revision, revision)
        self.assertEqual(self.history.pending_paths(), [PATH])

    def test_cloud_name_survives_stale_logs_and_same_name_refreshes(self):
        self.history.observe(samples())
        self.history.apply_metadata(PATH, {'name': '云端新名称', 'state': 'RUNNING'})
        revision = self.history.revision
        self.history.observe(samples())
        self.history.apply_metadata(PATH, {'name': '云端新名称', 'state': 'RUNNING'})
        detail = self.history.detail(PATH)
        self.assertEqual(detail['name'], '云端新名称')
        self.assertEqual(detail['original_name'], '初始实验')
        self.assertEqual(detail['name_source'], 'cloud')
        self.assertEqual(detail['history_count'], 1)
        self.assertEqual(detail['rename_history'][0]['old_name'], '初始实验')
        self.assertEqual(detail['rename_history'][0]['source'], 'cloud')
        self.assertEqual(self.history.revision, revision)

    def test_verified_rename_uses_previously_verified_cloud_name(self):
        self.history.observe(samples())
        self.history.apply_metadata(PATH, {'name': '最终名称'}, source='rename', previous_name='云端原名')
        self.history.apply_metadata(PATH, {'name': '最终名称'}, source='rename', previous_name='云端原名')
        events = self.history.detail(PATH)['rename_history']
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]['old_name'], events[0]['new_name'], events[0]['source']),
                         ('云端原名', '最终名称', 'rename'))

    def test_failed_rename_does_not_record_change(self):
        self.history.observe(samples())
        revision = self.history.revision
        for row in ({'ok': False, 'name': '未保存', 'code': 'conflict'},
                    {'name': '未保存', 'error': 'network failure'}):
            self.assertFalse(self.history.apply_metadata(PATH, row, source='rename', previous_name='初始实验'))
        self.assertEqual(self.history.detail(PATH)['history_count'], 0)
        self.assertEqual(self.history.detail(PATH)['name'], '初始实验')
        self.assertEqual(self.history.revision, revision)

    def test_only_explicit_successful_cloud_state_means_completed(self):
        self.history.observe(samples())
        for state in ('RUNNING', 'CRASHED', 'ABORTED', 'OFFLINE', 'COMPLETED', 'unknown', None, {}):
            self.history.apply_metadata(PATH, {'name': '实验', 'state': state, 'finished_at': 100})
            self.assertEqual(self.history.search()['total'], 0, state)
        self.history.apply_metadata(PATH, {'state': 'finished', 'finished_at': 200})
        detail = self.history.search()['items'][0]
        self.assertEqual(detail['state'], 'FINISHED')
        self.assertEqual(detail['finished_at'], 200)
        self.assertNotIn(PATH, self.history.pending_paths())

    def test_success_without_finish_time_uses_confirmation_time_without_fabricating_finish(self):
        self.history.observe(samples())
        with patch('experiment_history.time.time', return_value=1234):
            self.history.apply_metadata(PATH, {'name': '已完成', 'state': 'FINISHED'})
        with patch('experiment_history.time.time', return_value=2345):
            self.history.apply_metadata(PATH, {'name': '已完成', 'state': 'FINISHED'})
        detail = self.history.search()['items'][0]
        self.assertIsNone(detail['finished_at'])
        self.assertEqual(detail['completed_at'], 1234)
        self.assertEqual(detail['completed_confirmed_at'], 1234)

    def test_return_to_running_exits_completed_history_and_clears_old_finish(self):
        self.history.observe(samples())
        self.history.apply_metadata(PATH, {'state': 'FINISHED', 'finished_at': 100})
        self.assertEqual(self.history.search()['total'], 1)
        self.history.apply_metadata(PATH, {'state': 'RUNNING', 'finished_at': 100})
        detail = self.history.detail(PATH)
        self.assertEqual(self.history.search()['total'], 0)
        self.assertIn(PATH, self.history.pending_paths())
        self.assertIsNone(detail['finished_at'])
        self.assertIsNone(detail['completed_at'])
        self.history.apply_metadata(PATH, {'state': 'FINISHED'})
        self.assertIsNone(self.history.search()['items'][0]['finished_at'])

    def test_invalid_metadata_cannot_overwrite_valid_fields(self):
        self.history.observe(samples())
        self.history.apply_metadata(PATH, {'name': '已完成', 'state': 'FINISHED', 'finished_at': 123})
        revision = self.history.revision
        self.history.apply_metadata(PATH, {'name': ' ', 'state': 'UNKNOWN', 'finished_at': 'invalid'})
        self.history.apply_metadata(PATH, {'name': 'bad\nname', 'state': None, 'finished_at': float('nan')})
        self.history.apply_metadata(PATH, {'error': 'timeout', 'name': 'bad', 'state': 'RUNNING'})
        detail = self.history.detail(PATH)
        self.assertEqual((detail['name'], detail['state'], detail['finished_at']), ('已完成', 'FINISHED', 123))
        self.assertEqual(self.history.revision, revision)

    def test_sdk_timestamps_are_normalized_to_utc_seconds(self):
        self.history.observe(samples())
        self.history.apply_metadata(PATH, {'state': 'FINISHED', 'created_at': '2026-02-01T08:00:00+08:00',
                                          'finished_at': '2026-02-01T01:00:00.125Z'})
        detail = self.history.detail(PATH)
        self.assertEqual(detail['created_at'], datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
        self.assertEqual(detail['finished_at'], datetime(2026, 2, 1, 1, 0, 0, 125000, tzinfo=timezone.utc).timestamp())
        self.history.apply_metadata(PATH, {'created_at': '2026-02-01T00:00:00'})
        self.assertEqual(self.history.detail(PATH)['created_at'], detail['created_at'])

    def test_search_matches_old_names_projects_and_machines_and_paginates(self):
        for index in range(7):
            path = 'researcher/Training/run-%s' % index
            self.history.observe(samples(path, name='中文旧名 %s' % index))
            self.history.apply_metadata(path, {'name': 'Current %s' % index, 'state': 'FINISHED', 'finished_at': index + 1})
        first = self.history.search()
        self.assertEqual(first['total'], 7)
        self.assertEqual(len(first['items']), 5)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['items'][0]['name'], 'Current 6')
        second = self.history.search(limit=5, offset=5)
        self.assertEqual(len(second['items']), 2)
        self.assertFalse(second['has_more'])
        self.assertEqual(self.history.search('中文旧名')['total'], 7)
        self.assertEqual(self.history.search('TRAINING')['total'], 7)
        self.assertEqual(self.history.search('current 6')['total'], 1)
        self.assertEqual(self.history.search('内蒙训练机')['total'], 7)
        self.assertEqual(self.history.search('重庆')['total'], 0)
        self.assertEqual(self.history.search("%' OR 1=1 --")['total'], 0)

    def test_query_wildcards_are_literal_and_unicode_casefold_is_supported(self):
        self.history.observe(samples(name='Straße_100%'))
        self.history.apply_metadata(PATH, {'state': 'FINISHED'})
        self.assertEqual(self.history.search('STRASSE')['total'], 1)
        self.assertEqual(self.history.search('_100%')['total'], 1)
        self.assertEqual(self.history.search('_100_')['total'], 0)

    def test_completed_order_uses_real_finish_or_confirmation_as_available(self):
        self.history.observe(samples())
        other = 'researcher/training/other-run'
        self.history.observe(samples(other))
        with patch('experiment_history.time.time', return_value=200):
            self.history.apply_metadata(PATH, {'state': 'FINISHED', 'finished_at': 100})
        with patch('experiment_history.time.time', return_value=150):
            self.history.apply_metadata(other, {'state': 'FINISHED'})
        self.assertEqual([item['path'] for item in self.history.search()['items']], [other, PATH])

    def test_same_slug_is_isolated_by_project_and_workspace(self):
        paths = [PATH, 'researcher/another/first-run', 'another/training/first-run']
        for index, path in enumerate(paths):
            self.history.observe(samples(path, name='实验%s' % index))
        self.history.apply_metadata(paths[1], {'name': '只改这个', 'state': 'FINISHED'})
        self.assertEqual(self.history.detail(paths[0])['name'], '实验0')
        self.assertEqual(self.history.detail(paths[2])['name'], '实验2')
        self.assertEqual(self.history.search()['total'], 1)

    def test_multiple_machines_are_retained_and_changes_invalidate_revision(self):
        self.history.observe(samples())
        revision = self.history.revision
        self.history.observe(samples(machine='machine-b', machine_name='重庆训练机', region='重庆区'))
        self.assertGreater(self.history.revision, revision)
        self.history.observe(samples(machine_name='内蒙训练机改名'))
        self.assertEqual({item['name'] for item in self.history.detail(PATH)['instances']},
                         {'内蒙训练机改名', '重庆训练机'})

    def test_pending_refreshes_skip_terminal_runs_but_keep_offline_and_rotate_errors(self):
        paths = ['researcher/training/run-%s' % index for index in range(4)]
        for path in paths:
            self.history.observe(samples(path))
        self.history.apply_metadata(paths[1], {'state': 'CRASHED'})
        self.history.apply_metadata(paths[2], {'state': 'ABORTED'})
        self.history.apply_metadata(paths[3], {'state': 'OFFLINE'})
        self.assertEqual(self.history.pending_paths(limit=1), [paths[0]])
        self.history.apply_metadata(paths[0], {'error': 'network failure'})
        self.assertEqual(self.history.pending_paths(), [paths[3], paths[0]])

    def test_sync_refreshes_all_states_and_rotates_successful_and_failed_reads(self):
        paths = ['researcher/training/run-%s' % index for index in range(5)]
        states = ('RUNNING', 'CRASHED', 'ABORTED', 'FINISHED', 'OFFLINE')
        for index, (path, state) in enumerate(zip(paths, states)):
            self.history.observe(samples(path))
            with patch('experiment_history.time.time', return_value=index + 10):
                self.history.apply_metadata(path, {'state': state})
        self.assertEqual(self.history.sync_paths(), paths)
        with patch('experiment_history.time.time', return_value=20):
            self.history.apply_metadata(paths[0], {'name': '最新云端名称'})
        self.assertEqual(self.history.sync_paths(), paths[1:] + [paths[0]])
        with patch('experiment_history.time.time', return_value=21):
            self.history.apply_metadata(paths[1], {'error': 'network failure'})
        self.assertEqual(self.history.sync_paths(), paths[2:] + paths[:2])
        self.assertEqual(self.history.sync_paths(limit=2), paths[2:4])
        self.assertEqual(self.history.pending_paths(), [paths[4], paths[0]])

    def test_new_runs_take_sync_priority_over_previously_read_runs(self):
        self.history.observe(samples())
        self.history.apply_metadata(PATH, {'state': 'FINISHED'})
        other = 'researcher/training/other-run'
        self.history.observe(samples(other))
        self.assertEqual(self.history.sync_paths(), [other, PATH])

    def test_cloud_metadata_before_log_sample_keeps_the_first_log_name(self):
        self.history.apply_metadata(PATH, {'name': '当前云端名', 'state': 'RUNNING'})
        self.history.observe(samples(name='最初日志名'))
        detail = self.history.detail(PATH)
        self.assertEqual(detail['name'], '当前云端名')
        self.assertEqual(detail['original_name'], '最初日志名')
        self.assertEqual(detail['name_source'], 'cloud')

    def test_missing_names_can_be_filled_without_a_fake_rename_event(self):
        self.history.observe(samples(name=''))
        self.history.apply_metadata(PATH, {'name': '真实名称'})
        self.assertEqual(self.history.detail(PATH)['history_count'], 0)
        self.assertEqual(self.history.detail(PATH)['name'], '真实名称')

    def test_invalid_paths_and_failed_unknown_records_are_not_created(self):
        self.history.observe([{'id': 'machine-a', 'tasks': [{'experiments': [
            {'workspace': 'researcher/another', 'project': 'training', 'run_id': 'run'},
            {'workspace': 'researcher', 'project': '..', 'run_id': 'run'},
        ]}]}])
        self.assertFalse(self.history.apply_metadata('invalid', {'name': 'name'}))
        self.assertFalse(self.history.apply_metadata(PATH, {'error': 'not found'}))
        self.assertFalse(self.history.contains(PATH))
        self.assertIsNone(self.history.detail(PATH))
        self.assertEqual(self.history.pending_paths(), [])

    def test_threaded_writes_and_reads_share_one_consistent_database(self):
        def save(index):
            path = 'researcher/training/run-%s' % index
            self.history.observe(samples(path))
            self.history.apply_metadata(path, {'name': '名称 %s' % index, 'state': 'FINISHED'})
            self.assertEqual(self.history.detail(path)['history_count'], 1)
            self.history.search(limit=5)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(save, range(24)))
        self.assertEqual(self.history.search()['total'], 24)


class ExperimentHistoryPersistenceTests(unittest.TestCase):
    def test_restart_preserves_names_completion_and_rename_history_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'history.sqlite3'
            first = ExperimentHistory(database)
            try:
                first.observe(samples())
                first.apply_metadata(PATH, {'name': '新名称', 'state': 'FINISHED', 'finished_at': 123},
                                     source='rename', previous_name='初始实验')
            finally:
                first.close()
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            second = ExperimentHistory(database)
            try:
                second.observe(samples())
                self.assertTrue(second.contains(PATH))
                self.assertEqual(second.detail(PATH)['name'], '新名称')
                self.assertEqual(second.detail(PATH)['name_source'], 'cloud')
                self.assertEqual(second.detail(PATH)['history_count'], 1)
                self.assertEqual(second.search('初始实验')['total'], 1)
            finally:
                second.close()

    def test_default_database_respects_config_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'AUTODL_CONFIG_DIR': directory}):
                history = ExperimentHistory()
            try:
                self.assertEqual(history.database, str(Path(directory) / 'experiment-history.sqlite3'))
            finally:
                history.close()

    def test_corrupt_database_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'history.sqlite3'
            payload = b'not a database; preserve this file'
            database.write_bytes(payload)
            with self.assertRaises(sqlite3.DatabaseError):
                ExperimentHistory(database)
            self.assertEqual(database.read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
