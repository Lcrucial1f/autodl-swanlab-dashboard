"""Cloud experiment names, independent of GPU collection and local run logs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent


def experiment_path(experiment):
    parts = [experiment.get(k) for k in ('workspace', 'project', 'run_id')]
    if any(not isinstance(s, str) or not s or '/' in s or s in ('.', '..')
           or len(s) > 512 or any(ord(c) < 32 for c in s) for s in parts):
        return ''
    return '/'.join(parts)


def validate_name(name):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise ValueError('实验名称需为 1–100 个字符')
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError('实验名称不能包含换行或控制字符')
    return name.strip()


def run_bridge(payload, runtime=None):
    from swanlab_config import get_runtime
    try:
        runtime = runtime or get_runtime()
    except (ValueError, OSError):
        return {'ok': False, 'error': 'SwanLab 本地配置或 Python 路径无效，请重新配置', 'code': 'unavailable'}
    interpreter = runtime['python']
    request = dict(payload)
    if runtime.get('api_key'):
        request['api_key'] = runtime['api_key']
    fallback = {'ok': False, 'error': '无法连接 SwanLab，请检查本机登录状态与 SDK 环境', 'code': 'unavailable'}
    output = ''
    try:
        result = subprocess.run([interpreter, '-B', str(ROOT / 'swanlab_bridge.py')],
                                input=json.dumps(request), capture_output=True, text=True, timeout=25)
        output = result.stdout
    except subprocess.TimeoutExpired as error:
        # communicate() includes flushed progress even when the watchdog kills the child.
        output = error.stdout or ''
        fallback = {'ok': False, 'error': 'SwanLab 请求超时，结果尚未确认，请刷新后核对', 'code': 'timeout'}
    except OSError:
        pass
    if isinstance(output, bytes):
        output = output.decode('utf-8', errors='replace')
    completed, answer = {}, None
    for line in output.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        row = message.get('progress')
        if isinstance(row, dict) and row.get('path') in payload.get('paths', []):
            completed[row['path']] = {key: row[key] for key in
                                      ('name', 'error', 'state', 'finished_at', 'created_at') if key in row}
        elif isinstance(message.get('ok'), bool):
            answer = message
    if payload.get('action') != 'read':
        return answer or fallback
    if answer and answer.get('ok') and isinstance(answer.get('experiments'), dict):
        completed.update(answer['experiments'])
    if not completed:
        return answer or fallback
    error = answer if answer and not answer.get('ok') else fallback
    for path in payload.get('paths', []):
        completed.setdefault(path, {'error': error['error'], 'code': error['code'], 'deferred': True})
    return {'ok': True, 'experiments': completed}


class ExperimentNames:
    def __init__(self, bridge=None, history=None):
        self.bridge = bridge or run_bridge
        self.history = history
        self.history_error = ''
        self.cache = {}
        self.lock = threading.Lock()
        self.operation = threading.Lock()
        self.wake = threading.Event()

    def refresh(self, paths):
        paths = list(dict.fromkeys(p for p in paths if p))[:100]
        if not paths:
            return
        # Serialize refreshes with mutations so an old response cannot undo a rename.
        with self.operation:
            result = self.bridge({'action': 'read', 'paths': paths})
            entries = result.get('experiments', {}) if result.get('ok') else {}
            history_errors = []
            for path in paths:
                row = entries.get(path) or {'error': result.get('error') or '云端读取失败'}
                if not row.get('deferred'):
                    error = self.record_metadata(path, row)
                    if error:
                        history_errors.append(error)
            if history_errors:
                self.history_error = history_errors[0]
            with self.lock:
                for path in paths:
                    row = entries.get(path, {})
                    old = self.cache.get(path, {})
                    if row.get('name'):
                        self.cache[path] = {'name': row['name'], 'synced_at': time.time(), 'error': ''}
                    else:
                        self.cache[path] = dict(old, error=row.get('error') or result.get('error') or '云端名称读取失败')
                # Keep only the active set plus a bounded recent cache.
                if len(self.cache) > 200:
                    self.cache = {p: self.cache[p] for p in paths if p in self.cache}

    def record_metadata(self, path, row, source='cloud', previous_name=None):
        if self.history is None:
            return ''
        try:
            self.history.apply_metadata(path, row, source=source, previous_name=previous_name)
            self.history_error = ''
        except (OSError, sqlite3.Error):
            self.history_error = '实验历史保存失败，请检查本地配置目录的权限和可用空间'
        return self.history_error

    def loop(self, provider):
        while True:
            self.wake.clear()
            try:
                self.refresh(provider())
            except Exception:
                pass
            self.wake.wait(30)

    def decorate(self, experiment):
        result = copy.deepcopy(experiment)
        path = experiment_path(result)
        result['path'] = path
        with self.lock:
            cached = dict(self.cache.get(path, {}))
        if cached.get('name'):
            result['name'] = cached['name']
            result['name_source'] = 'cache' if cached.get('error') else 'cloud'
        else:
            result['name_source'] = 'log'
            if self.history is not None and path:
                try:
                    saved = self.history.detail(path)
                    if saved and saved.get('name_source') == 'cloud':
                        result['name'] = saved['name']
                        result['name_source'] = 'cache'
                except (OSError, sqlite3.Error):
                    self.history_error = '实验历史读取失败，请检查本地配置目录'
        result['name_synced_at'] = cached.get('synced_at')
        result['name_sync_error'] = cached.get('error', '')
        return result

    def rename(self, path, name, expected_name):
        name = validate_name(name)
        if not isinstance(expected_name, str) or not expected_name:
            raise ValueError('缺少修改前的名称，请刷新页面后重试')
        with self.operation:
            response = self.bridge({'action': 'rename', 'path': path, 'name': name,
                                    'expected_name': expected_name})
            row = response.get('experiment', {})
            if response.get('ok') and row.get('name'):
                warning = self.record_metadata(path, row, source='rename', previous_name=expected_name)
                if warning:
                    response = dict(response, warning='SwanLab 名称已更新，但' + warning)
                with self.lock:
                    self.cache[path] = {'name': row['name'], 'synced_at': time.time(), 'error': ''}
            else:
                self.wake.set()
            return response
