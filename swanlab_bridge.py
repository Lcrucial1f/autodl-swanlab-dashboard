"""SwanLab metadata adapter. Requests arrive on stdin; credentials stay in SDK."""
import contextlib
from datetime import datetime
import io
import json
import sys
from urllib.parse import quote

from experiment_names import validate_name


def route_for(path):
    if not isinstance(path, str):
        raise ValueError('实验路径无效')
    parts = path.split('/')
    if len(parts) != 3 or any(not p or p in ('.', '..') or len(p) > 512
                              or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError('实验路径无效')
    workspace, project, slug = (quote(p, safe='') for p in parts)
    return '/project/%s/%s/runs/%s' % (workspace, project, slug)


def metadata(client, path):
    # Client.get has no timeout argument; request forwards it to the SDK session.
    data = client.request('GET', route_for(path), retries=0, timeout=5, log_error=False).data
    if not isinstance(data, dict) or not data.get('name') or not data.get('cuid'):
        raise ValueError('SwanLab 未返回完整实验信息')
    state = data.get('state')
    return {
        'path': path, 'name': data['name'], 'cuid': data['cuid'],
        'state': state.strip().upper() if isinstance(state, str) else '',
        # These are the same fields exposed by the SDK's Run properties.
        'created_at': timestamp_seconds(data.get('createdAt')),
        'finished_at': timestamp_seconds(data.get('finishedAt')),
    }


def timestamp_seconds(value):
    """Parse cloud ISO 8601 dates without guessing a missing timezone."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        if date.tzinfo is None:
            return None
        return date.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def failure(error):
    status = getattr(getattr(error, 'response', None), 'status_code', None)
    if status in (401, 403):
        message = 'SwanLab 登录失效或没有修改权限，请检查本机账号'
    elif status == 404:
        message = 'SwanLab 实验不存在或当前账号无法访问'
    else:
        message = 'SwanLab 请求未完成，请检查网络或登录状态后重试'
    return {'ok': False, 'error': message, 'code': 'remote_error'}


def execute(payload, client, progress=None):
    action = payload.get('action')
    if action == 'read':
        paths = payload.get('paths', [])
        if not isinstance(paths, list) or len(paths) > 100:
            raise ValueError('实验列表无效')
        results = {}
        for path in paths:
            try:
                results[path] = metadata(client, path)
            except Exception as error:
                results[path] = failure(error)
            if progress:
                progress(path, results[path])
        return {'ok': True, 'experiments': results}
    if action == 'rename':
        path = payload.get('path')
        name = validate_name(payload.get('name'))
        current = metadata(client, path)
        if current['name'] == name:
            return {'ok': True, 'experiment': current}
        if current['name'] != payload.get('expected_name'):
            return {'ok': False, 'code': 'conflict',
                    'error': 'SwanLab 名称已被修改，请关闭弹窗并刷新后重试', 'current_name': current['name']}
        return rename_remote(client, path, current, name)
    raise ValueError('不支持的操作')


def rename_remote(client, path, current, name):
    # Same metadata-only PUT used by SwanLab's experiment-name editor.
    # Resolve the URL slug first; the update endpoint takes the internal cuid.
    endpoint = route_for(path).rsplit('/', 1)[0] + '/' + quote(current['cuid'], safe='')
    client.put(endpoint, data={'name': name}, retries=0)
    verified = metadata(client, path)
    if verified['name'] != name:
        return {'ok': False, 'code': 'unconfirmed',
                'error': 'SwanLab 已接收请求，但名称尚未确认，请刷新后核对'}
    return {'ok': True, 'experiment': verified}


def main():
    output = sys.stdout

    def progress(path, row):
        # Only allow display metadata through while SDK output remains suppressed.
        item = {'path': path}
        item.update({key: row[key] for key in
                     ('name', 'state', 'created_at', 'finished_at', 'error') if key in row})
        print(json.dumps({'progress': item}, ensure_ascii=False), file=output, flush=True)

    try:
        payload = json.load(sys.stdin)
        # SDK diagnostics can contain request details; never forward them to UI/logs.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import swanlab
            api_key = payload.get('api_key')
            if not api_key:
                # Refuse credentials belonging to another deployment.
                api_key, api_host, _ = swanlab.Api._resolve_credentials(None, None)
                if api_host.rstrip('/') not in ('https://api.swanlab.cn', 'https://swanlab.cn'):
                    raise ValueError('仅支持 SwanLab 公有云账号')
            api = swanlab.Api(api_key=api_key, host='https://swanlab.cn')
            if not api.username:
                result = {'ok': False, 'code': 'not_logged_in', 'error': 'SwanLab 凭据无效或登录请求未完成'}
            elif payload.get('action') == 'status':
                result = {'ok': True, 'username': api.username, 'host': 'https://swanlab.cn'}
            else:
                result = execute(payload, api._ctx.client, progress=progress)
    except (ImportError, AttributeError):
        result = {'ok': False, 'code': 'sdk_missing', 'error': '当前 Python 未安装兼容的 SwanLab SDK'}
    except ValueError:
        result = {'ok': False, 'code': 'invalid', 'error': 'SwanLab 配置或实验信息无效，请检查本机登录状态后重试'}
    except Exception as error:
        result = {'ok': False, 'code': 'not_logged_in', 'error': '请先登录 SwanLab'} if type(error).__name__ == 'AuthenticationError' else failure(error)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
