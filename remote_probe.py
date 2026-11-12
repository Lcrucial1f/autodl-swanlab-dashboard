"""Read-only AutoDL GPU / process / SwanLab collector, sent over SSH stdin."""
import csv
import datetime
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote


def run(args, timeout=5):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def number(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def read_processes():
    result = {}
    boot = time.time() - float(Path('/proc/uptime').read_text().split()[0])
    ticks = os.sysconf('SC_CLK_TCK')
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            argv = (path / 'cmdline').read_bytes().decode(errors='replace').strip('\0').split('\0')
            if not argv or not argv[0]:
                continue
            result[int(path.name)] = {
                'pid': int(path.name), 'ppid': int(stat[1]), 'argv': argv,
                'start': boot + int(stat[19]) / ticks,
                'cwd': os.readlink(path / 'cwd'),
                'exe': os.readlink(path / 'exe'),
            }
        except (OSError, ValueError, IndexError):
            continue
    return result


def family_root(pid, processes):
    current = pid
    visited = set()
    while current in processes and current not in visited:
        visited.add(current)
        parent = processes[current]['ppid']
        p = processes.get(parent)
        if not p or not any(x in Path(p['exe']).name for x in ('python', 'torchrun', 'pt_elastic')):
            break
        current = parent
    return current


def candidate_dirs(members):
    roots = set()
    for p in members:
        cwd = Path(p['cwd'])
        roots.add(cwd)
        args = p['argv']
        for n, arg in enumerate(args):
            option, _, value = arg.partition('=')
            if option in ('--output', '--out_dir', '--output_dir', '--logdir', '--log_dir', '--save_dir', '--save-dir'):
                value = value or (args[n + 1] if n + 1 < len(args) else '')
                if value:
                    roots.add(Path(value) if value.startswith('/') else cwd / value)
        for fd in ('1', '2'):
            try:
                path = os.readlink('/proc/%s/fd/%s' % (p['pid'], fd))
                if path.startswith('/') and Path(path).is_file():
                    roots.add(Path(path).parent)
            except OSError:
                pass
    # Inspect known log locations only, never recursively walk datasets/checkpoints.
    dirs = set()
    for root in roots:
        for p in (root, root / 'swanlog', root / 'logs' / 'swanlog'):
            if p.is_dir():
                dirs.add(p)
    return dirs


HEADER_READER = r'''
import json,sys
from swanlab.sdk.internal.core_python.store import DataStoreReader
from swanlab.proto.swanlab.record.v1.record_pb2 import Record
reader=DataStoreReader()
try:
 reader.open(sys.argv[1])
 for index,raw in enumerate(reader):
  rec=Record(); rec.ParseFromString(raw)
  if rec.HasField('start'):
   s=rec.start
   print('__SWAN_HEADER__'+json.dumps(dict(project=s.project,workspace=s.workspace,name=s.name,run_id=s.id)))
   break
  if index>=5: break
finally:
 if reader._fp is not None: reader.close()
'''


def experiment_for(members, hostname):
    pids = {p['pid']: p for p in members}
    matches = []
    for directory in candidate_dirs(members):
        try:
            runs = sorted(directory.glob('run-*'), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
        except OSError:
            continue
        for folder in runs:
            meta_path = folder / 'files/swanlab-metadata.json'
            try:
                meta = json.loads(meta_path.read_text())
                runtime = meta.get('runtime', meta)
                pid = int(runtime.get('pid', 0))
                process = pids.get(pid)
                if not process or runtime.get('hostname') != hostname:
                    continue
                if runtime.get('cwd') and runtime['cwd'] != process['cwd']:
                    continue
                stamp = re.match(r'run-(\d{8}_\d{6})-(.+)', folder.name)
                if not stamp:
                    continue
                started = datetime.datetime.strptime(stamp[1], '%Y%m%d_%H%M%S').timestamp()
                # Reject logs left by an older process that reused the same PID.
                if started < process['start'] - 10 or started > time.time() + 60:
                    continue
                matches.append((folder, process, stamp[2]))
            except (OSError, ValueError, TypeError):
                continue
    unique = {str(folder): (folder, p, rid) for folder, p, rid in matches}
    # A long-lived worker can create several sequential runs. Without a unique
    # active run, do not silently select the newest log or mislabel an old run.
    if len(unique) > 1:
        return []
    results = []
    for folder, p, rid in list(unique.values())[:8]:
        info = {'run_id': rid, 'name': rid, 'project': '', 'workspace': '',
                'evidence': 'SwanLab 日志 PID、主机、目录与进程启动时间匹配',
                'log_dir': str(folder), 'url': ''}
        binary = folder / ('run-' + rid + '.swanlab')
        try:
            # Use the already-running task's Python environment; install nothing.
            interpreter = p['argv'][0]
            if not (Path(interpreter).is_absolute() and 'python' in Path(interpreter).name):
                interpreter = p['exe']
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            proc = subprocess.run([interpreter, '-B', '-c', HEADER_READER, str(binary)],
                                  capture_output=True, text=True, timeout=5, env=env)
            for line in proc.stdout.splitlines():
                if line.startswith('__SWAN_HEADER__'):
                    header = json.loads(line[len('__SWAN_HEADER__'):])
                    if header.get('run_id') == rid:
                        info.update(header)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        if info['project'] and info['workspace']:
            info['url'] = 'https://swanlab.cn/@%s/%s/runs/%s/chart' % tuple(
                quote(info[k], safe='') for k in ('workspace', 'project', 'run_id'))
        results.append(info)
    return results


def collect():
    hostname = os.uname().nodename
    query = run(['nvidia-smi', '--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu', '--format=csv,noheader,nounits'])
    gpus = []
    for row in csv.reader(io.StringIO(query.stdout)):
        if len(row) == 7 and row[0].strip().isdigit():
            gpus.append(dict(zip(['index', 'uuid', 'name', 'total', 'used', 'free', 'util'],
                                 [int(row[0]), row[1].strip(), row[2].strip()] + [number(s) for s in row[3:]])))
    apps = run(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_gpu_memory', '--format=csv,noheader,nounits'])
    if apps.returncode:
        apps = run(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_memory', '--format=csv,noheader,nounits'])
    gpu_pids = {}
    for row in csv.reader(io.StringIO(apps.stdout)):
        if len(row) == 3 and row[1].strip().isdigit():
            gpu_pids.setdefault(int(row[1]), set()).add(row[0].strip())
    procs = read_processes() if gpu_pids else {}
    roots = {family_root(pid, procs) for pid in gpu_pids}
    tasks = []
    for root in sorted(roots):
        members = [p for p in procs.values() if family_root(p['pid'], procs) == root]
        member_pids = {p['pid'] for p in members} | {root}
        uuids = set().union(*(gpu_pids.get(pid, set()) for pid in member_pids))
        experiments = experiment_for(members, hostname) if members else []
        main = procs.get(root, {})
        tasks.append({'id': '%s:%s' % (root, main.get('start', 0)), 'pid': root,
                      'started_at': main.get('start'), 'cwd': main.get('cwd', ''),
                      'gpu_indices': [g['index'] for g in gpus if g['uuid'] in uuids],
                      'experiments': experiments, 'process_visible': bool(members)})
    for g in gpus:
        has_process = any(g['uuid'] in values for values in gpu_pids.values())
        if has_process or (g['util'] is not None and g['util'] >= 5) or (g['used'] is not None and g['used'] >= 512):
            g['state'] = 'busy'
        elif g['used'] is not None and g['util'] is not None and apps.returncode == 0:
            g['state'] = 'idle'
        else:
            g['state'] = 'unknown'
    return {'hostname': hostname, 'gpus': gpus, 'tasks': tasks,
            'gpu_error': '' if gpus else 'GPU 查询失败或当前未挂载 GPU',
            'process_error': '' if apps.returncode == 0 else 'GPU 进程查询不可用'}


if __name__ == '__main__':
    try:
        print('__AUTODL_DASHBOARD__' + json.dumps(collect(), ensure_ascii=False))
    except Exception as error:
        print('__AUTODL_DASHBOARD__' + json.dumps({'collector_error': type(error).__name__}))
