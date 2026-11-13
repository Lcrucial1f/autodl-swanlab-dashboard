import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from swanlab_bridge import execute, main, metadata


PATH = 'researcher/training/short-run'
CREATED = '2026-01-01T00:00:00.125Z'
FINISHED = '2026-01-01T02:00:00.500+02:00'


class CloudRunStateTests(unittest.TestCase):
    def client_for(self, **fields):
        client = Mock()
        client.request.return_value = SimpleNamespace(data={
            'name': '示例实验', 'cuid': 'internal-cuid', **fields,
        })
        return client

    def test_official_camelcase_dates_become_epoch_seconds(self):
        client = self.client_for(state='FINISHED', createdAt=CREATED, finishedAt=FINISHED)
        result = metadata(client, PATH)
        self.assertEqual(result, {
            'path': PATH, 'name': '示例实验', 'cuid': 'internal-cuid',
            'state': 'FINISHED', 'created_at': 1767225600.125,
            'finished_at': 1767225600.5,
        })
        client.put.assert_not_called()

    def test_all_remote_states_are_preserved_without_inventing_success(self):
        for state in ('RUNNING', 'FINISHED', 'CRASHED', 'ABORTED', 'OFFLINE', 'UNKNOWN'):
            with self.subTest(state=state):
                result = metadata(self.client_for(state=' ' + state.lower() + ' '), PATH)
                self.assertEqual(result['state'], state)
                self.assertIsNone(result['finished_at'])

    def test_missing_or_invalid_state_remains_unknown(self):
        for state in (None, '', 0, True, {}, []):
            with self.subTest(state=state):
                result = metadata(self.client_for(state=state, finishedAt=FINISHED), PATH)
                self.assertEqual(result['state'], '')
                self.assertEqual(result['finished_at'], 1767225600.5)

    def test_unfinished_run_keeps_null_finish_time(self):
        result = metadata(self.client_for(state='RUNNING', createdAt=CREATED, finishedAt=None), PATH)
        self.assertEqual(result['state'], 'RUNNING')
        self.assertIsNone(result['finished_at'])

    def test_optional_dates_do_not_break_valid_name_metadata(self):
        for value in (None, '', 'not a date', '2026-02-30T00:00:00Z',
                      '2026-01-01T00:00:00', '2026-01-01', 1767225600, True, {}):
            with self.subTest(value=value):
                result = metadata(self.client_for(createdAt=value, finishedAt=value), PATH)
                self.assertEqual(result['name'], '示例实验')
                self.assertIsNone(result['created_at'])
                self.assertIsNone(result['finished_at'])

    def test_missing_optional_fields_are_compatible_with_older_responses(self):
        result = metadata(self.client_for(), PATH)
        self.assertEqual(result['state'], '')
        self.assertIsNone(result['created_at'])
        self.assertIsNone(result['finished_at'])

    def test_failed_read_does_not_report_a_completion(self):
        client = Mock()
        client.request.side_effect = TimeoutError('unavailable')
        emitted = []
        result = execute({'action': 'read', 'paths': [PATH]}, client,
                         progress=lambda path, row: emitted.append(row))
        self.assertFalse(result['experiments'][PATH]['ok'])
        self.assertNotIn('state', emitted[0])
        self.assertNotIn('finished_at', emitted[0])
        client.put.assert_not_called()

    def run_main(self, payload, client):
        api = SimpleNamespace(username='researcher', _ctx=SimpleNamespace(client=client))
        sdk = SimpleNamespace(Api=Mock(return_value=api))
        output = io.StringIO()
        with patch.dict('sys.modules', {'swanlab': sdk}), \
                patch('sys.stdin', io.StringIO(json.dumps({'api_key': 'test-key', **payload}))), \
                patch('sys.stdout', output):
            main()
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_progress_preserves_completion_fields_and_excludes_internal_metadata(self):
        client = self.client_for(state='FINISHED', createdAt=CREATED, finishedAt=FINISHED)
        messages = self.run_main({'action': 'read', 'paths': [PATH]}, client)
        self.assertEqual(messages[0], {'progress': {
            'path': PATH, 'name': '示例实验', 'state': 'FINISHED',
            'created_at': 1767225600.125, 'finished_at': 1767225600.5,
        }})
        self.assertTrue(messages[1]['ok'])
        self.assertEqual(messages[1]['experiments'][PATH]['state'], 'FINISHED')

    def test_status_still_checks_login_without_loading_experiments(self):
        client = Mock()
        messages = self.run_main({'action': 'status'}, client)
        self.assertEqual(messages, [{
            'ok': True, 'username': 'researcher', 'host': 'https://swanlab.cn',
        }])
        client.request.assert_not_called()
        client.put.assert_not_called()


if __name__ == '__main__':
    unittest.main()
