import http.client
import json
import threading
import unittest
from unittest.mock import patch

import server


ITEM = {'id': 7, 'name': 'training', 'region': 'north', 'host': 'machine.example.invalid', 'port': 2200, 'user': 'root'}


class SetupApiTests(unittest.TestCase):
    def setUp(self):
        self.handler = type('TestHandler', (server.Handler,), {'monitor': server.Monitor(), 'token': 'test-token'})
        self.http = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), self.handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    def request(self, path, body, token=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        headers = {'Host': '127.0.0.1:%s' % server.PORT, 'Content-Type': 'application/json'}
        if token:
            headers['X-Autodl-Token'] = 'test-token'
        connection.request('POST', path, json.dumps(body), headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_all_setup_writes_require_local_token(self):
        with patch.object(server.CLI, 'create_instance') as create, patch.object(server.swanlab_config, 'configure') as configure:
            self.assertEqual(self.request('/api/instances/add', {'command': 'ssh root@example.invalid'}, token=False)[0], 403)
            self.assertEqual(self.request('/api/swanlab/configure', {'api_key': 'test-secret'}, token=False)[0], 403)
        create.assert_not_called()
        configure.assert_not_called()

    def test_password_only_reaches_key_install_and_is_never_returned(self):
        with patch.object(server.CLI, 'create_instance', return_value=ITEM) as create, patch.object(server.CLI, 'install_key') as install:
            status, body = self.request('/api/instances/add', {'command': 'ssh root@example.invalid', 'password': 'test-secret'})
        self.assertEqual(status, 200)
        self.assertTrue(body['key_installed'])
        create.assert_called_once_with(command='ssh root@example.invalid')
        install.assert_called_once_with(ITEM, password='test-secret')
        self.assertNotIn('test-secret', json.dumps(body))

    def test_failed_ssh_setup_keeps_registered_item_and_actionable_warning(self):
        with patch.object(server.CLI, 'create_instance', return_value=ITEM), patch.object(server.CLI, 'install_key', side_effect=ValueError('sensitive error')):
            status, body = self.request('/api/instances/add', {'command': 'ssh root@example.invalid', 'password': 'test-secret'})
        self.assertEqual(status, 200)
        self.assertFalse(body['key_installed'])
        self.assertEqual(body['instance']['id'], 7)
        self.assertIn('autodl key 7', body['warning'])
        self.assertNotIn('sensitive error', json.dumps(body))

    def test_existing_key_only_uses_noninteractive_connection_test(self):
        with patch.object(server.CLI, 'create_instance', return_value=ITEM), patch.object(server, 'test_connection', return_value={'ok': True, 'online': True, 'message': 'OK'}), patch.object(server.CLI, 'install_key') as install:
            status, body = self.request('/api/instances/add', {'command': 'ssh root@example.invalid', 'password': ''})
        self.assertEqual(status, 200)
        self.assertTrue(body['key_installed'])
        install.assert_not_called()

    def test_known_region_display_name_reuses_existing_region(self):
        with patch.object(server.CLI, 'create_instance', return_value=ITEM) as create, patch.object(server, 'test_connection', return_value={'ok': True, 'online': True}), patch.object(server, 'get_settings', return_value=server.DEFAULT):
            self.assertEqual(self.request('/api/instances/add', {'command': 'ssh root@example.invalid', 'region': '内蒙区'})[0], 200)
        self.assertEqual(create.call_args.kwargs['region'], 'nmb2')

    def test_remove_only_updates_local_registry_and_removes_sample(self):
        self.handler.monitor.samples['7'] = {'tasks': []}
        with patch.object(server.CLI, 'load', return_value={'instances': [ITEM]}), patch.object(server.CLI, 'remove_instance') as remove, patch.object(server, 'test_connection') as connect:
            self.assertEqual(self.request('/api/instances/remove', {'id': 7})[0], 200)
        remove.assert_called_once_with('7')
        connect.assert_not_called()
        self.assertNotIn('7', self.handler.monitor.samples)

    def test_account_change_clears_prior_name_cache_only_after_success(self):
        self.handler.monitor.names.cache['path'] = {'name': 'old'}
        with patch.object(server.swanlab_config, 'configure', return_value={'ok': False, 'state': 'not_logged_in', 'message': '验证失败'}):
            self.assertEqual(self.request('/api/swanlab/configure', {'api_key': 'test-secret'})[0], 422)
        self.assertIn('path', self.handler.monitor.names.cache)
        with patch.object(server.swanlab_config, 'configure', return_value={'ok': True, 'state': 'connected', 'username': 'researcher'}):
            status, body = self.request('/api/swanlab/configure', {'api_key': 'test-secret'})
        self.assertEqual(status, 200)
        self.assertEqual(body['swanlab']['username'], 'researcher')
        self.assertEqual(self.handler.monitor.names.cache, {})
        self.assertTrue(self.handler.monitor.names.wake.is_set())

    def test_public_instance_never_passes_arbitrary_local_credentials(self):
        self.assertEqual(server.public_instance(dict(ITEM, password='secret', api_key='secret')), ITEM)


if __name__ == '__main__':
    unittest.main()
