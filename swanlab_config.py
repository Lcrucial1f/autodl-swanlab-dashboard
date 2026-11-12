"""Local SwanLab account setup and portable SDK discovery."""
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
HOST = 'https://swanlab.cn'
_lock = threading.RLock()
_status_cache = {}
_runtime_cache = {}
_UNSET = object()


def config_dir():
    return Path(os.environ.get('AUTODL_CONFIG_DIR', '~/.config/autodl')).expanduser()


def _read(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError('SwanLab 本地配置无法读取，请检查配置文件') from None
    if not isinstance(value, dict):
        raise ValueError('SwanLab 本地配置格式不正确')
    return value


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _python_path(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('Python 路径无效')
    value = value.strip()
    if not value:
        return ''
    path = Path(value).expanduser()
    if not path.is_absolute():
        discovered = shutil.which(value)
        if not discovered:
            raise ValueError('找不到指定的 Python，请填写可执行文件的完整路径')
        path = Path(discovered)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('指定的 Python 不存在或不可执行')
    # Keep a virtual environment's entry path instead of resolving its symlink.
    return str(path.absolute())


def _has_sdk(interpreter):
    try:
        result = subprocess.run(
            [interpreter, '-B', '-c', "import importlib.util; print('available' if importlib.util.find_spec('swanlab') else 'missing')"],
            capture_output=True, text=True, timeout=4,
        )
        return result.returncode == 0 and result.stdout.strip() == 'available'
    except (OSError, subprocess.TimeoutExpired):
        return False


def _launcher_python():
    launcher = shutil.which('swanlab')
    if not launcher:
        return ''
    try:
        with open(launcher, encoding='utf-8') as source:
            line = source.readline(4096)
        if not line.startswith('#!'):
            return ''
        words = shlex.split(line[2:].strip())
        if words and Path(words[0]).name == 'env':
            words = [word for word in words[1:] if not word.startswith('-')]
            return shutil.which(words[0]) or '' if words else ''
        return words[0] if words else ''
    except (OSError, UnicodeError, ValueError):
        return ''


def resolve_python(configured=None):
    """Honor explicit choices, otherwise select the first environment with the SDK."""
    explicit = os.environ.get('AUTODL_SWANLAB_PYTHON', '').strip()
    if explicit:
        return _python_path(explicit)
    if configured is None:
        configured = _read(config_dir() / 'swanlab-settings.json').get('python', '')
    if configured:
        return _python_path(configured)
    cache_key = (str(ROOT), sys.executable, os.environ.get('PATH', ''))
    with _lock:
        cached = _runtime_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
    candidates = [ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'),
                  sys.executable, _launcher_python(), shutil.which('python3'), shutil.which('python')]
    seen = set()
    selected = sys.executable
    for candidate in candidates:
        if not candidate or str(candidate) in seen:
            continue
        seen.add(str(candidate))
        if Path(candidate).is_file() and _has_sdk(str(candidate)):
            selected = str(candidate)
            break
    with _lock:
        _runtime_cache[cache_key] = (time.monotonic(), selected)
    return selected


def get_runtime(python=None, api_key=_UNSET):
    credential_path = config_dir() / 'swanlab-credentials.json'
    saved_key = _read(credential_path).get('api_key') if api_key is _UNSET else None
    if saved_key is not None and (not isinstance(saved_key, str) or not saved_key.strip()):
        raise ValueError('SwanLab 本地凭据格式不正确，请重新登录')
    key = saved_key if api_key is _UNSET else api_key
    source = 'local' if key else 'environment' if os.environ.get('SWANLAB_API_KEY') else 'system'
    return {'python': resolve_python(python), 'api_key': key, 'credential_source': source,
            'configured': bool(saved_key) or credential_path.exists()}


def _status(runtime):
    # Lazy import avoids a cycle with the bridge's runtime discovery.
    from experiment_names import run_bridge
    result = run_bridge({'action': 'status'}, runtime=runtime)
    connected = bool(result.get('ok') and result.get('username'))
    code = result.get('code', 'unavailable')
    state = 'connected' if connected else code if code in ('sdk_missing', 'not_logged_in') else 'unavailable'
    messages = {
        'connected': 'SwanLab 已连接，实验名称会自动同步',
        'not_logged_in': '尚未登录或凭据失效，请填写 SwanLab API Key，或在终端运行 swanlab login',
        'sdk_missing': '当前 Python 未安装兼容的 SwanLab SDK，请运行 python3 install.py 或选择已安装 SDK 的 Python',
        'unavailable': '暂时无法验证 SwanLab，请检查网络、账号和 Python 配置后重试',
    }
    return {'ok': connected, 'state': state, 'username': result.get('username', '') if connected else '',
            'host': HOST, 'python': runtime['python'], 'credential_source': runtime['credential_source'],
            'configured': runtime['configured'], 'message': messages[state]}


def _cache_key():
    return (str(config_dir()), os.environ.get('AUTODL_SWANLAB_PYTHON', ''),
            bool(os.environ.get('SWANLAB_API_KEY')))


def setup_status(refresh=False):
    with _lock:
        key = _cache_key()
        cached = _status_cache.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < 20:
            return copy.deepcopy(cached[1])
        try:
            status = _status(get_runtime())
        except (OSError, ValueError):
            status = {'ok': False, 'state': 'unavailable', 'username': '', 'host': HOST,
                      'python': '', 'credential_source': 'none', 'configured': False,
                      'message': 'SwanLab 本地配置无法读取或 Python 路径无效，请重新配置'}
        _status_cache[key] = (time.monotonic(), status)
        return copy.deepcopy(status)


def configure(data):
    if not isinstance(data, dict) or not data or set(data) - {'python', 'api_key'}:
        raise ValueError('SwanLab 配置请求格式不正确')
    if 'api_key' in data and (not isinstance(data['api_key'], str) or len(data['api_key']) > 4096
                              or any(ord(c) <= 32 or ord(c) == 127 for c in data['api_key'].strip())):
        raise ValueError('API Key 格式无效，请完整复制密钥')
    with _lock:
        saved = {} if 'python' in data else _read(config_dir() / 'swanlab-settings.json')
        python = _python_path(data['python']) if 'python' in data else saved.get('python', '')
        supplied_key = data.get('api_key', '').strip()
        runtime = get_runtime(python=python, api_key=supplied_key) if supplied_key else get_runtime(python=python)
        status = _status(runtime)
        if not status['ok']:
            return status
        _write(config_dir() / 'swanlab-settings.json', {'python': python})
        if supplied_key:
            _write(config_dir() / 'swanlab-credentials.json', {'api_key': supplied_key})
            status['configured'] = True
            status['credential_source'] = 'local'
        _status_cache.clear()
        _status_cache[_cache_key()] = (time.monotonic(), status)
        return copy.deepcopy(status)


def clear_credentials():
    with _lock:
        try:
            (config_dir() / 'swanlab-credentials.json').unlink()
        except FileNotFoundError:
            pass
        _status_cache.clear()
        result = setup_status(refresh=True)
        result['cleared'] = True
        return result
