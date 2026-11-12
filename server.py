#!/usr/bin/env python3
"""Local-only AutoDL dashboard. No third-party server dependencies."""
import concurrent.futures
import copy
import http.server
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from experiment_names import ExperimentNames, experiment_path
import autodl as CLI
import swanlab_config

ROOT = Path(__file__).resolve().parent
BASE = CLI.BASE
SETTINGS = BASE / 'dashboard-settings.json'
PORT = int(os.environ.get('AUTODL_PORT', '8766'))
if not 1 <= PORT <= 65535:
    raise ValueError('AUTODL_PORT 必须为 1–65535 的端口号')
URL = 'http://127.0.0.1:%s' % PORT
CONFIG_TAG = hashlib.sha256(str(BASE.resolve()).encode()).hexdigest()[:16]
INTERVAL = 6
DEFAULT = {'regions': [{'id': 'nmb2', 'name': '内蒙区'}, {'id': 'cqa1', 'name': '重庆区'}], 'instances': {}}
PROBE = (ROOT / 'remote_probe.py').read_text()
PYTHON_COMMAND = '''for p in /root/miniconda3/bin/python /opt/conda/bin/python /usr/bin/python3; do
  if [ -x "$p" ]; then exec "$p" -B -; fi
done
if command -v python3 >/dev/null 2>&1; then exec python3 -B -; fi
echo '__NO_PYTHON__' >&2; exit 127'''


def get_settings():
    return CLI.read_json(SETTINGS, copy.deepcopy(DEFAULT))


def public_instance(item):
    return {key: item[key] for key in ('id', 'name', 'host', 'port', 'user', 'region', 'identity_file') if key in item}


def setup_snapshot():
    key = CLI.public_key_path(CLI.KEY)
    return {'config_dir': str(BASE), 'registry_path': str(CLI.REGISTRY),
            'instances': [public_instance(item) for item in CLI.load()['instances']],
            'ssh': {'available': bool(shutil.which('ssh')),
                    'expect_available': bool(shutil.which('expect')),
                    'key_exists': CLI.KEY.is_file(),
                    'public_key': key.read_text().strip() if key.is_file() else ''},
            'swanlab': swanlab_config.setup_status(), 'platform': platform.system()}


def test_connection(item):
    if not shutil.which('ssh'):
        return {'ok': True, 'online': False, 'message': '请先安装 OpenSSH 客户端'}
    try:
        result = subprocess.run(CLI.ssh_args(item) + ['true'], capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=15)
        return {'ok': True, 'online': result.returncode == 0,
                'message': 'SSH 免密连接成功' if result.returncode == 0 else CLI.classify(result.stderr)}
    except subprocess.TimeoutExpired:
        return {'ok': True, 'online': False, 'message': '连接超时，请检查实例运行状态和网络'}


def validate_settings(data):
    if not isinstance(data, dict) or set(data) != {'regions', 'instances'}:
        raise ValueError('设置格式不正确')
    regions, overrides = data['regions'], data['instances']
    if not isinstance(regions, list) or not 1 <= len(regions) <= 30 or not isinstance(overrides, dict):
        raise ValueError('请保留 1–30 个区域')
    seen = set()
    for region in regions:
        if not isinstance(region, dict) or set(region) != {'id', 'name'}:
            raise ValueError('区域格式不正确')
        key, name = region['id'], region['name']
        if not isinstance(key, str) or not key or len(key) > 80 or key in seen:
            raise ValueError('区域编号重复或无效')
        if not isinstance(name, str) or not name.strip() or len(name) > 30:
            raise ValueError('区域名称限 1–30 个字符')
        seen.add(key)
    for key, item in overrides.items():
        if not isinstance(key, str) or not key.isdigit() or not isinstance(item, dict):
            raise ValueError('实例设置不正确')
        if set(item) != {'name', 'region'} or item['region'] not in seen:
            raise ValueError('实例区域不存在')
        if not isinstance(item['name'], str) or len(item['name']) > 60:
            raise ValueError('实例名称过长')
    return data


def probe(item):
    checked = time.time()
    try:
        p = subprocess.run(CLI.ssh_args(item) + [PYTHON_COMMAND], input=PROBE,
                           capture_output=True, text=True, timeout=24)
        marker = '__AUTODL_DASHBOARD__'
        lines = [line[len(marker):] for line in p.stdout.splitlines() if line.startswith(marker)]
        if not lines:
            error = '远端缺少 Python' if '__NO_PYTHON__' in p.stderr else CLI.classify(p.stderr)
            return {'online': False, 'checked_at': checked, 'error': error, 'gpus': [], 'tasks': []}
        result = json.loads(lines[-1])
        if result.get('collector_error'):
            return {'online': True, 'checked_at': checked, 'error': '采集失败：' + result['collector_error'], 'gpus': [], 'tasks': []}
        return dict(result, online=True, checked_at=time.time(), error='')
    except subprocess.TimeoutExpired:
        return {'online': False, 'checked_at': checked, 'error': '连接或采集超时', 'gpus': [], 'tasks': []}
    except (OSError, ValueError):
        return {'online': False, 'checked_at': checked, 'error': '采集结果无法读取', 'gpus': [], 'tasks': []}


class Monitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.samples = {}
        self.updated = 0
        self.refreshing = True
        self.error = ''
        self.names = ExperimentNames()

    def experiment_paths(self):
        with self.lock:
            return {experiment_path(e) for sample in self.samples.values()
                    for task in sample.get('tasks', []) for e in task.get('experiments', [])} - {''}

    def loop(self):
        observed_paths = set()
        while True:
            self.wake.clear()
            with self.lock:
                self.refreshing = True
            try:
                items = CLI.load()['instances']
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futures = {pool.submit(probe, i): i for i in items}
                    for future in concurrent.futures.as_completed(futures):
                        item = futures[future]
                        sample = future.result()
                        with self.lock:
                            old = self.samples.get(str(item['id']), {})
                            if not sample['online']:
                                sample['last_seen'] = old.get('checked_at') if old.get('online') else old.get('last_seen')
                                sample['last_gpu_count'] = len(old.get('gpus', [])) or old.get('last_gpu_count', 0)
                            self.samples[str(item['id'])] = sample
                with self.lock:
                    current_ids = {str(item['id']) for item in items}
                    self.samples = {key: value for key, value in self.samples.items() if key in current_ids}
                    self.updated = time.time()
                    self.error = ''
                paths = self.experiment_paths()
                if paths != observed_paths:
                    self.names.wake.set()
                    observed_paths = paths
            except Exception as error:
                with self.lock:
                    self.error = '实例清单读取失败：' + type(error).__name__
            finally:
                with self.lock:
                    self.refreshing = False
            self.wake.wait(INTERVAL)

    def snapshot(self):
        settings = get_settings()
        regions = copy.deepcopy(settings['regions'])
        region_ids = {r['id'] for r in regions}
        items = []
        with self.lock:
            samples = copy.deepcopy(self.samples)
            updated, refreshing, error = self.updated, self.refreshing, self.error
        for item in CLI.load()['instances']:
            override = settings['instances'].get(str(item['id']), {})
            region = override.get('region', item['region'])
            if region not in region_ids:
                regions.append({'id': region, 'name': region.upper()})
                region_ids.add(region)
            sample = samples.get(str(item['id']), {'online': None, 'gpus': [], 'tasks': [], 'checked_at': None, 'error': ''})
            for task in sample.get('tasks', []):
                task['experiments'] = [self.names.decorate(e) for e in task.get('experiments', [])]
            age = time.time() - (sample.get('checked_at') or time.time())
            items.append(dict(public_instance(item), **sample, display_name=override.get('name') or '实例 ' + str(item['port']),
                              region=region, stale=age > 30))
        return {'instances': items, 'regions': regions, 'updated_at': updated,
                'refreshing': refreshing, 'interval': INTERVAL, 'error': error, 'setup_needed': not items}


class Handler(http.server.BaseHTTPRequestHandler):
    monitor = None
    token = secrets.token_urlsafe(32)

    def log_message(self, *args):
        pass

    def permitted_host(self):
        return self.headers.get('Host') in ('127.0.0.1:%s' % PORT, 'localhost:%s' % PORT)

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.permitted_host():
            return self.send(403, {'error': '本地访问限定'})
        path = self.path.split('?')[0]
        if path == '/health':
            return self.send(200, {'app': 'autodl-dashboard', 'version': 2, 'config_tag': CONFIG_TAG})
        if path == '/api/setup':
            try:
                return self.send(200, setup_snapshot())
            except (OSError, ValueError, KeyError):
                return self.send(500, {'error': '本地配置无法读取，请检查配置文件格式和访问权限'})
        if path == '/api/state':
            try:
                return self.send(200, self.monitor.snapshot())
            except (OSError, ValueError, KeyError):
                return self.send(500, {'error': '本地配置无法读取，请检查实例清单'})
        if path == '/':
            return self.send(200, (ROOT / 'static/index.html').read_text().replace('__TOKEN__', self.token), 'text/html; charset=utf-8')
        static = {'/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/icon.svg': ('icon.svg', 'image/svg+xml')}
        if path in static:
            name, mime = static[path]
            return self.send(200, (ROOT / 'static' / name).read_bytes(), mime)
        self.send(404, {'error': '不存在'})

    def do_POST(self):
        if not self.permitted_host() or self.headers.get('X-Autodl-Token') != self.token:
            return self.send(403, {'error': '请从本地看板操作'})
        origin = self.headers.get('Origin')
        if origin and origin not in (URL, 'http://localhost:%s' % PORT):
            return self.send(403, {'error': '来源不匹配'})
        if self.path.startswith('/api/instances/') or self.path.startswith('/api/swanlab/'):
            return self.setup_action()
        if self.path == '/api/refresh':
            self.monitor.wake.set()
            self.monitor.names.wake.set()
            return self.send(200, {'ok': True})
        if self.path == '/api/experiments/rename':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8000:
                    raise ValueError('请求无效')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or set(data) != {'path', 'name', 'expected_name'}:
                    raise ValueError('修改请求格式不正确')
                if not isinstance(data['path'], str) or data['path'] not in self.monitor.experiment_paths():
                    return self.send(404, {'error': '该实验已不在当前实例列表中，请刷新后重试'})
                result = self.monitor.names.rename(data['path'], data['name'], data['expected_name'])
                status = 200 if result.get('ok') else 409 if result.get('code') == 'conflict' else 502
                return self.send(status, result)
            except (ValueError, TypeError, KeyError) as error:
                return self.send(400, {'error': str(error)})
        if self.path == '/api/settings':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 32000:
                    raise ValueError('请求过大或为空')
                data = validate_settings(json.loads(self.rfile.read(length)))
                CLI.atomic_json(SETTINGS, data)
                return self.send(200, {'ok': True})
            except (ValueError, KeyError, TypeError):
                return self.send(400, {'error': '设置格式无效，请检查区域名称和实例归属'})
        self.send(404, {'error': '不存在'})

    def read_body(self, limit=16000):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= limit:
            raise ValueError('请求为空或过大')
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError('请求格式不正确')
        return data

    def setup_action(self):
        try:
            data = self.read_body()
            if self.path == '/api/instances/add':
                fields = {'command', 'name', 'region', 'identity_file', 'password'}
                if set(data) - fields or not isinstance(data.get('command'), str):
                    raise ValueError('请填写 SSH 连接指令')
                if any(not isinstance(value, str) for value in data.values()):
                    raise ValueError('连接配置需要使用文本')
                password = data.pop('password', '')
                if len(password) > 4096 or any(c in password for c in ('\n', '\r', '\x00')):
                    raise ValueError('SSH 密码格式无效')
                region = data.get('region', '').strip()
                known_regions = get_settings()['regions']
                if region and region not in {r['id'] for r in known_regions}:
                    matches = [r['id'] for r in known_regions if r['name'] == region]
                    if len(matches) == 1:
                        data['region'] = matches[0]
                item = CLI.create_instance(**data)
                self.monitor.wake.set()
                warning = ''
                installed = False
                try:
                    if password:
                        CLI.install_key(item, password=password)
                        installed = True
                    else:
                        result = test_connection(item)
                        installed = result['online']
                        if not installed:
                            warning = result['message'] + '；实例已登记，可运行 autodl key %s 配置免密' % item['id']
                except (ValueError, OSError, subprocess.SubprocessError):
                    warning = '实例已登记，免密配置未完成；请检查 SSH 密码、expect 是否安装，或运行 autodl key %s 重试' % item['id']
                finally:
                    password = ''
                self.monitor.wake.set()
                return self.send(200, {'ok': True, 'instance': public_instance(item),
                                       'key_installed': installed, 'warning': warning})
            if self.path in ('/api/instances/remove', '/api/instances/test'):
                if set(data) != {'id'} or not isinstance(data['id'], (str, int)) or isinstance(data['id'], bool):
                    raise ValueError('请提供实例编号')
                item = CLI.resolve(CLI.load(), str(data['id']))
                if self.path.endswith('/test'):
                    return self.send(200, test_connection(item))
                CLI.remove_instance(str(item['id']))
                with self.monitor.lock:
                    self.monitor.samples.pop(str(item['id']), None)
                self.monitor.wake.set()
                self.monitor.names.wake.set()
                return self.send(200, {'ok': True})
            if self.path == '/api/swanlab/check':
                if data:
                    raise ValueError('检查请求不需要参数')
                return self.send(200, {'ok': True, 'swanlab': swanlab_config.setup_status(refresh=True)})
            if self.path in ('/api/swanlab/configure', '/api/swanlab/clear'):
                if self.path.endswith('/clear') and data:
                    raise ValueError('清除请求不需要参数')
                with self.monitor.names.operation:
                    result = (swanlab_config.clear_credentials() if self.path.endswith('/clear')
                              else swanlab_config.configure(data))
                    if result.get('ok') or self.path.endswith('/clear'):
                        with self.monitor.names.lock:
                            self.monitor.names.cache.clear()
                        self.monitor.names.wake.set()
                if self.path.endswith('/configure') and not result.get('ok'):
                    return self.send(422, {'ok': False, 'error': result.get('message', '登录验证未成功'), 'swanlab': result})
                return self.send(200, {'ok': True, 'swanlab': result})
            return self.send(404, {'error': '不存在'})
        except (ValueError, TypeError, KeyError) as error:
            return self.send(400, {'error': str(error)})
        except (OSError, subprocess.SubprocessError):
            return self.send(500, {'error': '本地配置或连接操作失败，请检查文件权限与运行环境'})


def health():
    try:
        with urllib.request.urlopen(URL + '/health', timeout=1) as response:
            data = json.load(response)
            legacy = data.get('version') == 1 and BASE == Path.home() / '.config/autodl' and PORT == 8766
            return data.get('app') == 'autodl-dashboard' and (data.get('config_tag') == CONFIG_TAG or legacy)
    except (OSError, ValueError):
        return False


def serve():
    monitor = Monitor()
    Handler.monitor = monitor
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=monitor.loop, daemon=True).start()
    threading.Thread(target=monitor.names.loop, args=(monitor.experiment_paths,), daemon=True).start()
    threading.Thread(target=swanlab_config.setup_status, daemon=True).start()
    print(URL, flush=True)
    server.serve_forever()


def launch(open_browser=True, show_setup=False):
    if not health():
        BASE.mkdir(parents=True, exist_ok=True)
        logfile = BASE / 'dashboard.log'
        fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as log:
            proc = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), 'serve'],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        CLI.atomic_json(BASE / 'dashboard-process.json', {'pid': proc.pid, 'port': PORT})
        for _ in range(40):
            if health():
                break
            if proc.poll() is not None:
                raise RuntimeError('看板启动失败；检查 %s（端口可能被占用）' % logfile)
            time.sleep(.15)
        else:
            raise RuntimeError('看板启动超时，请检查 %s' % logfile)
    print('AutoDL 看板：' + URL)
    if open_browser:
        webbrowser.open(URL + '/#setup' if show_setup else URL)


def stop():
    if not health():
        print('看板未运行。')
        return
    data = CLI.read_json(BASE / 'dashboard-process.json', {})
    pid = data.get('pid')
    if not isinstance(pid, int):
        raise RuntimeError('缺少服务进程记录')
    command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout
    if str(Path(__file__).resolve()) not in command or 'serve' not in command:
        raise RuntimeError('进程身份不匹配，未停止任何进程')
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if not health():
            break
        time.sleep(.1)
    print('看板已停止；云端训练继续运行。')


if __name__ == '__main__':
    try:
        action = sys.argv[1] if len(sys.argv) > 1 else 'start'
        if action == 'serve':
            serve()
        elif action == 'stop':
            stop()
        elif action in ('start', '--no-open'):
            launch(open_browser=action != '--no-open')
        elif action == 'setup':
            launch(show_setup=True)
        else:
            raise ValueError('用法：autodl ui [stop]')
    except (RuntimeError, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
