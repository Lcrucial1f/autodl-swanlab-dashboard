"""Private, persistent run identities, confirmed names and completion history."""
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import quote


SUCCESS_STATES = frozenset(('FINISHED',))
TERMINAL_STATES = SUCCESS_STATES | frozenset(('CRASHED', 'ABORTED'))
# OFFLINE can be uploaded later, so it remains eligible for a cloud refresh.
KNOWN_STATES = TERMINAL_STATES | frozenset(('RUNNING', 'OFFLINE'))


def _parts(path):
    if not isinstance(path, str):
        return None
    parts = path.split('/')
    if len(parts) != 3 or any(not s or s in ('.', '..') or len(s) > 512
                              or any(ord(c) < 32 for c in s) for s in parts):
        return None
    return parts


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value.strip()


def _timestamp(value):
    """Accept SDK ISO strings and epoch seconds; invalid/missing values stay absent."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        elif isinstance(value, datetime):
            result = (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
        elif isinstance(value, str) and value.strip():
            stamp = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
            result = (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()
        else:
            return None
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def _bound(value, default, minimum, maximum):
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError, OverflowError):
        return default


class ExperimentHistory:
    """Only a successful cloud state establishes completion; SSH silence never does."""

    def __init__(self, database=None):
        if database is None:
            base = Path(os.environ.get('AUTODL_CONFIG_DIR', '~/.config/autodl')).expanduser()
            database = base / 'experiment-history.sqlite3'
        self.database = str(database)
        self.lock = threading.RLock()
        self._revision = 0
        if self.database != ':memory:':
            destination = Path(self.database).expanduser()
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            destination.chmod(0o600)
            self.database = str(destination)
        self.connection = sqlite3.connect(self.database, timeout=10, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function('CASEFOLD', 1, lambda value: (value or '').casefold())
        try:
            self.connection.execute('PRAGMA foreign_keys=ON')
            with self.connection:
                self.connection.executescript('''
                    CREATE TABLE IF NOT EXISTS experiments (
                        path TEXT PRIMARY KEY,
                        workspace TEXT NOT NULL,
                        project TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        name TEXT NOT NULL DEFAULT '',
                        name_source TEXT NOT NULL DEFAULT 'log',
                        original_name TEXT NOT NULL DEFAULT '',
                        url TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'UNKNOWN',
                        created_at REAL,
                        finished_at REAL,
                        completed_at REAL,
                        first_seen REAL NOT NULL,
                        last_seen REAL NOT NULL,
                        metadata_checked_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS experiment_instances (
                        path TEXT NOT NULL REFERENCES experiments(path),
                        instance_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        region TEXT NOT NULL,
                        PRIMARY KEY (path, instance_id)
                    );
                    CREATE TABLE IF NOT EXISTS rename_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT NOT NULL REFERENCES experiments(path),
                        old_name TEXT NOT NULL,
                        new_name TEXT NOT NULL,
                        changed_at REAL NOT NULL,
                        source TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS rename_events_path ON rename_events(path, id);
                    CREATE INDEX IF NOT EXISTS experiments_state ON experiments(state);
                ''')
        except Exception:
            self.connection.close()
            raise

    @property
    def revision(self):
        with self.lock:
            return self._revision

    def _insert(self, path, now, name=''):
        workspace, project, run_id = _parts(path)
        url = 'https://swanlab.cn/@' + '/'.join(quote(part, safe='') for part in (workspace, project))
        url += '/runs/' + quote(run_id, safe='')
        self.connection.execute('''
            INSERT INTO experiments (path, workspace, project, run_id, name, url, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (path, workspace, project, run_id, name, url, now, now))

    def observe(self, instances):
        """Remember log identities without allowing stale logs to undo cloud names."""
        now, changed = time.time(), False
        with self.lock, self.connection:
            for instance in instances or []:
                if not isinstance(instance, dict):
                    continue
                instance_id = str(instance.get('id') or '')
                instance_name = _name(instance.get('display_name')) or _name(instance.get('name')) or instance_id
                region = _name(instance.get('region')) or ''
                for task in instance.get('tasks') or []:
                    if not isinstance(task, dict):
                        continue
                    for experiment in task.get('experiments') or []:
                        if not isinstance(experiment, dict):
                            continue
                        identity = [experiment.get(key) for key in ('workspace', 'project', 'run_id')]
                        if any(not isinstance(value, str) or '/' in value for value in identity):
                            continue
                        path = '/'.join(identity)
                        if not _parts(path):
                            continue
                        log_name = _name(experiment.get('name')) or ''
                        row = self.connection.execute('SELECT * FROM experiments WHERE path=?', (path,)).fetchone()
                        if row is None:
                            self._insert(path, now, log_name)
                            changed = True
                        self.connection.execute('UPDATE experiments SET last_seen=? WHERE path=?', (now, path))
                        if log_name and (row is None or not row['original_name']):
                            self.connection.execute('UPDATE experiments SET original_name=? WHERE path=?', (log_name, path))
                            if row is not None and not row['name']:
                                self.connection.execute('UPDATE experiments SET name=? WHERE path=?', (log_name, path))
                            changed = True
                        if not instance_id:
                            continue
                        machine = self.connection.execute('''
                            SELECT name, region FROM experiment_instances WHERE path=? AND instance_id=?
                        ''', (path, instance_id)).fetchone()
                        if machine is None or (machine['name'], machine['region']) != (instance_name, region):
                            self.connection.execute('''
                                INSERT INTO experiment_instances (path, instance_id, name, region) VALUES (?, ?, ?, ?)
                                ON CONFLICT(path, instance_id) DO UPDATE SET name=excluded.name, region=excluded.region
                            ''', (path, instance_id, instance_name, region))
                            changed = True
            if changed:
                self._revision += 1
        return changed

    def apply_metadata(self, path, row, source='cloud', previous_name=None):
        """Persist verified cloud metadata. Failed responses cannot add rename events."""
        if not _parts(path) or not isinstance(row, dict):
            return False
        if source not in ('cloud', 'rename'):
            raise ValueError('历史来源无效')
        now = time.time()
        with self.lock, self.connection:
            current = self.connection.execute('SELECT * FROM experiments WHERE path=?', (path,)).fetchone()
            if row.get('error') or row.get('ok') is False:
                if current is not None:
                    self.connection.execute('UPDATE experiments SET metadata_checked_at=? WHERE path=?', (now, path))
                return False
            name = _name(row.get('name'))
            state = row.get('state')
            state = state.strip().upper() if isinstance(state, str) else None
            state = state if state in KNOWN_STATES else None
            created_at = _timestamp(row.get('created_at'))
            finished_at = _timestamp(row.get('finished_at'))
            changed = current is None
            if current is None:
                if name is None and state is None:
                    return False
                self._insert(path, now)
                current = self.connection.execute('SELECT * FROM experiments WHERE path=?', (path,)).fetchone()
            values = dict(current)
            if name is not None:
                if name != current['name']:
                    old_name = (_name(previous_name) if source == 'rename' else None) or current['name']
                    if old_name and old_name != name:
                        self.connection.execute('''
                            INSERT INTO rename_events (path, old_name, new_name, changed_at, source)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (path, old_name, name, now, source))
                    values['name'] = name
                values['name_source'] = 'cloud'
            if state is not None:
                values['state'] = state
                if state in SUCCESS_STATES:
                    if current['state'] not in SUCCESS_STATES:
                        values['completed_at'] = now
                        values['finished_at'] = finished_at
                    elif finished_at is not None:
                        values['finished_at'] = finished_at
                else:
                    values['completed_at'] = None
                    values['finished_at'] = None
            elif finished_at is not None and current['state'] in SUCCESS_STATES:
                values['finished_at'] = finished_at
            if created_at is not None:
                values['created_at'] = created_at
            fields = ('name', 'name_source', 'state', 'created_at', 'finished_at', 'completed_at')
            changed = changed or any(values[key] != current[key] for key in fields)
            self.connection.execute('''
                UPDATE experiments SET name=?, name_source=?, state=?, created_at=?, finished_at=?,
                    completed_at=?, metadata_checked_at=? WHERE path=?
            ''', tuple(values[key] for key in fields) + (now, path))
            if changed:
                self._revision += 1
        return changed

    def _detail(self, row):
        if row is None:
            return None
        result = dict(row)
        result['name'] = result['name'] or result['run_id']
        result['instances'] = [dict(machine) for machine in self.connection.execute('''
            SELECT instance_id AS id, name, region FROM experiment_instances WHERE path=? ORDER BY region, name, instance_id
        ''', (row['path'],))]
        result['rename_history'] = [dict(event) for event in self.connection.execute('''
            SELECT old_name, new_name, changed_at, source FROM rename_events WHERE path=? ORDER BY id DESC
        ''', (row['path'],))]
        result['history_count'] = len(result['rename_history'])
        result['completed_confirmed_at'] = result['completed_at']
        return result

    def detail(self, path):
        with self.lock:
            return self._detail(self.connection.execute('SELECT * FROM experiments WHERE path=?', (path,)).fetchone())

    def search(self, query='', limit=5, offset=0):
        limit = _bound(limit, 5, 1, 100)
        offset = _bound(offset, 0, 0, 1000000000)
        query = query.strip().casefold() if isinstance(query, str) else ''
        where = "e.state = 'FINISHED'"
        args = []
        if query:
            where += ''' AND (
                instr(CASEFOLD(e.name), ?) > 0 OR instr(CASEFOLD(e.original_name), ?) > 0
                OR instr(CASEFOLD(e.project), ?) > 0 OR instr(CASEFOLD(e.workspace), ?) > 0
                OR instr(CASEFOLD(e.run_id), ?) > 0
                OR EXISTS (SELECT 1 FROM experiment_instances i WHERE i.path=e.path
                    AND (instr(CASEFOLD(i.name), ?) > 0 OR instr(CASEFOLD(i.region), ?) > 0))
                OR EXISTS (SELECT 1 FROM rename_events h WHERE h.path=e.path
                    AND (instr(CASEFOLD(h.old_name), ?) > 0 OR instr(CASEFOLD(h.new_name), ?) > 0))
            )'''
            args = [query] * 9
        with self.lock:
            total = self.connection.execute('SELECT count(*) FROM experiments e WHERE ' + where, args).fetchone()[0]
            rows = self.connection.execute('SELECT e.* FROM experiments e WHERE ' + where + '''
                ORDER BY COALESCE(e.finished_at, e.completed_at) DESC, e.path ASC LIMIT ? OFFSET ?
            ''', args + [limit, offset]).fetchall()
            return {'items': [self._detail(row) for row in rows], 'total': total, 'limit': limit,
                    'offset': offset, 'has_more': offset + len(rows) < total}

    def pending_paths(self, limit=50):
        limit = _bound(limit, 50, 1, 100)
        states = sorted(TERMINAL_STATES)
        with self.lock:
            return [row['path'] for row in self.connection.execute('''
                SELECT path FROM experiments WHERE state NOT IN (''' + ','.join('?' for _ in states) + ''')
                ORDER BY COALESCE(metadata_checked_at, 0), first_seen, path LIMIT ?
            ''', states + [limit])]

    def sync_paths(self, limit=100):
        """Refresh every saved run fairly, including terminal runs that may resume."""
        limit = _bound(limit, 100, 1, 100)
        with self.lock:
            return [row['path'] for row in self.connection.execute('''
                SELECT path FROM experiments
                ORDER BY COALESCE(metadata_checked_at, 0), first_seen, path LIMIT ?
            ''', (limit,))]

    def contains(self, path):
        with self.lock:
            return self.connection.execute('SELECT 1 FROM experiments WHERE path=?', (path,)).fetchone() is not None

    def close(self):
        with self.lock:
            self.connection.close()
