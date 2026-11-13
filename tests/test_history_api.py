import http.client
import json
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import server
from experiment_history import ExperimentHistory
from experiment_names import ExperimentNames, run_bridge


PATH = 'researcher/training/run-one'
EXPERIMENT = {'workspace': 'researcher', 'project': 'training', 'run_id': 'run-one', 'name': '初始实验'}
ITEM = {'id': 7, 'port': 2200, 'host': 'machine.example.invalid', 'user': 'root', 'region': 'north'}


class HistoryFixture(unittest.TestCase):
    def setUp(self):
        self.history = ExperimentHistory(':memory:')
        self.monitor = server.Monitor(history=self.history)
        self.monitor.samples['7'] = {'online': True, 'tasks': [{'experiments': [EXPERIMENT]}]}
        self.load = patch.object(server.CLI, 'load', return_value={'instances': [ITEM]}).start()
        patch.object(server, 'get_settings', return_value=server.DEFAULT).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.history.close)
        self.monitor.observe_history()


class HistoryIntegrationTests(HistoryFixture):
    def test_disappeared_task_is_polled_until_cloud_confirms_completion(self):
        self.monitor.names.bridge = Mock(return_value={'ok': True, 'experiments': {
            PATH: {'name': '云端名称', 'state': 'RUNNING'},
        }})
        self.monitor.names.refresh(self.monitor.sync_paths())
        self.monitor.samples['7'] = {'online': False, 'tasks': []}
        self.monitor.observe_history()
        self.assertEqual(self.history.search()['total'], 0)
        self.assertIn(PATH, self.monitor.sync_paths())
        self.monitor.names.bridge.return_value['experiments'][PATH] = {
            'name': '完成后的名称', 'state': 'FINISHED', 'finished_at': 1234567890,
        }
        self.monitor.names.refresh(self.monitor.sync_paths())
        result = self.history.search('初始实验')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['name'], '完成后的名称')
        self.assertEqual(result['items'][0]['instances'][0]['id'], '7')
        self.assertEqual(result['items'][0]['finished_at'], 1234567890)

    def test_success_records_once_but_failed_rename_adds_no_history(self):
        bridge = Mock(return_value={'ok': False, 'code': 'conflict', 'error': '已被其他人修改'})
        self.monitor.names.bridge = bridge
        self.monitor.names.rename(PATH, '新实验', '初始实验')
        self.assertEqual(self.history.detail(PATH)['history_count'], 0)
        bridge.return_value = {'ok': True, 'experiment': {'name': '新实验', 'state': 'RUNNING'}}
        self.monitor.names.rename(PATH, '新实验', '初始实验')
        self.monitor.names.rename(PATH, '新实验', '新实验')
        event = self.history.detail(PATH)['rename_history']
        self.assertEqual(len(event), 1)
        self.assertEqual((event[0]['old_name'], event[0]['new_name'], event[0]['source']),
                         ('初始实验', '新实验', 'rename'))

    def test_persisted_name_is_used_before_first_cloud_refresh(self):
        self.history.apply_metadata(PATH, {'name': '已确认名称', 'state': 'RUNNING'})
        names = ExperimentNames(history=self.history)
        self.assertEqual(names.decorate(EXPERIMENT)['name'], '已确认名称')
        self.assertEqual(names.decorate(EXPERIMENT)['name_source'], 'cache')

    def test_archived_run_is_rechecked_and_resume_removes_it_from_completed(self):
        self.history.apply_metadata(PATH, {'name': '初始实验', 'state': 'FINISHED'})
        self.monitor.samples.clear()
        self.assertIn(PATH, self.monitor.sync_paths())
        self.monitor.names.bridge = Mock(return_value={'ok': True, 'experiments': {
            PATH: {'name': '继续训练', 'state': 'RUNNING'},
        }})
        self.monitor.names.refresh(self.monitor.sync_paths())
        self.assertEqual(self.history.search()['total'], 0)
        self.assertIn(PATH, self.history.pending_paths())

    def test_progress_preserves_completed_state_when_watchdog_interrupts(self):
        row = {'path': PATH, 'name': '完成实验', 'state': 'FINISHED', 'finished_at': 1234567890,
               'created_at': 1234560000}
        error = server.subprocess.TimeoutExpired('bridge', 25, output=json.dumps({'progress': row}).encode())
        with patch('experiment_names.subprocess.run', side_effect=error):
            result = run_bridge({'action': 'read', 'paths': [PATH]}, runtime={'python': 'python3'})
        self.assertEqual(result['experiments'][PATH]['state'], 'FINISHED')
        self.assertEqual(result['experiments'][PATH]['finished_at'], 1234567890)

    def test_timed_out_batch_prioritizes_unread_runs_on_the_next_poll(self):
        other = 'researcher/training/run-two'
        self.history.apply_metadata(other, {'name': '等待确认', 'state': 'RUNNING'})
        self.monitor.names.bridge = Mock(return_value={'ok': True, 'experiments': {
            PATH: {'name': '初始实验', 'state': 'RUNNING'},
            other: {'error': '超时尚未读取', 'deferred': True},
        }})
        self.monitor.names.refresh(self.monitor.sync_paths())
        self.assertEqual(self.monitor.sync_paths()[0], other)

    def test_interrupted_and_finished_runs_remain_in_fair_polling_queue(self):
        for index in range(105):
            self.history.apply_metadata('researcher/training/live-%03d' % index,
                                        {'name': '任务', 'state': 'RUNNING'})
        self.history.apply_metadata(PATH, {'name': '初始实验', 'state': 'ABORTED'})
        seen = set()
        for _ in range(3):
            paths = self.monitor.sync_paths()
            seen.update(paths)
            for path in paths:
                self.history.apply_metadata(path, {'error': '不可访问'})
        self.assertIn(PATH, seen)
        self.assertEqual(len(seen), 106)

    def test_remote_success_and_local_write_failure_returns_warning(self):
        self.monitor.names.bridge = Mock(return_value={'ok': True, 'experiment': {'name': '新实验'}})
        with patch.object(self.history, 'apply_metadata', side_effect=sqlite3.OperationalError('private detail')):
            result = self.monitor.names.rename(PATH, '新实验', '初始实验')
        self.assertTrue(result['ok'])
        self.assertIn('warning', result)
        self.assertNotIn('private detail', result['warning'])
        self.assertTrue(self.monitor.snapshot()['history_error'])


class HistoryApiTests(HistoryFixture):
    def setUp(self):
        super().setUp()
        self.handler = type('TestHandler', (server.Handler,), {'monitor': self.monitor, 'token': 'test-token'})
        self.http = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    def request(self, path, body=None, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        headers = {'Host': host or '127.0.0.1:%s' % server.PORT, 'X-Autodl-Token': 'test-token'}
        connection.request('GET' if body is None else 'POST', path,
                           None if body is None else json.dumps(body), headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_default_paging_and_search_old_name(self):
        for index in range(8):
            path = 'researcher/training/run-%s' % index
            self.history.apply_metadata(path, {'name': 'old-%s' % index})
            self.history.apply_metadata(path, {'name': 'new-%s' % index, 'state': 'FINISHED',
                                               'finished_at': 1234567890 + index})
        status, data = self.request('/api/experiments/completed')
        self.assertEqual(status, 200)
        self.assertEqual((len(data['items']), data['total'], data['has_more']), (5, 8, True))
        self.assertEqual(data['items'][0]['name'], 'new-7')
        _, page = self.request('/api/experiments/completed?limit=5&offset=5')
        self.assertEqual((len(page['items']), page['has_more']), (3, False))
        _, match = self.request('/api/experiments/completed?' + urlencode({'q': 'OLD-3'}))
        self.assertEqual([item['name'] for item in match['items']], ['new-3'])

    def test_history_validates_input_and_host(self):
        status, data = self.request('/api/experiments/history?' + urlencode({'path': PATH}))
        self.assertEqual(status, 200)
        self.assertEqual(data['experiment']['original_name'], '初始实验')
        self.assertEqual(self.request('/api/experiments/history?path=missing')[0], 404)
        for query in ('limit=0', 'limit=101', 'offset=-1', 'limit=bad'):
            self.assertEqual(self.request('/api/experiments/completed?' + query)[0], 400)
        self.assertEqual(self.request('/api/experiments/completed', host='external.invalid')[0], 403)

    def test_history_failure_returns_safe_error_and_does_not_rename(self):
        with patch.object(self.history, 'search', side_effect=sqlite3.DatabaseError('private contents')):
            status, data = self.request('/api/experiments/completed')
            self.assertEqual(status, 500)
            self.assertNotIn('private contents', data['error'])
        self.monitor.names.bridge = Mock()
        with patch.object(self.history, 'observe', side_effect=sqlite3.OperationalError('disk full')):
            status, _ = self.request('/api/experiments/rename', {
                'path': PATH, 'name': '新实验', 'expected_name': '初始实验',
            })
        self.assertEqual(status, 503)
        self.monitor.names.bridge.assert_not_called()


if __name__ == '__main__':
    unittest.main()
