"""Project-wide activity from registered pipeline ledgers; never dispatch work.

Unlike the episode index, this includes images, audio and arbitrary job IDs.
Only public status fields leave the local reader; paths and prompts stay local.
"""
import copy
import hashlib
import json
import threading
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from companion_store import clean, identifier, sha
import production_index

LOCK = threading.RLock()
CACHE = {}
ACTIVE = frozenset(('claimed', 'submitted', 'running'))


def fingerprint(path):
    if path is None:
        return None
    try:
        stat = Path(path).stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
    except OSError:
        return None


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        return result if result > 0 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


@lru_cache(maxsize=2048)
def checked_receipt(path, expected, fingerprint):
    """Recheck changed files; avoid rehashing immutable receipts on every poll."""
    file = Path(path)
    if sha(file) != expected:
        return False, ''
    data = json.loads(file.read_text())
    if isinstance(data, dict):
        messages = [clean(data[k])[:500] for k in (
            'error_message', 'failed_reason', 'failure_reason', 'message',
            'error_code', 'browser_validation', 'error') if isinstance(data.get(k), str) and data[k].strip()]
        return True, ' · '.join(messages)[:700]
    return True, ''


def receipt_info(live):
    receipts = live.get('receipts', [])
    valid = bool(receipts)
    reason = ''
    for receipt in receipts:
        try:
            path = Path(receipt['path'])
            stat = path.stat()
            verified, message = checked_receipt(str(path), receipt['sha256'],
                (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
            valid = valid and verified
            if message:
                reason = message
        except (OSError, ValueError, KeyError, TypeError):
            valid = False
    return valid, reason


def plan_jobs(path, statepath, project):
    plan = json.loads(path.read_text())
    if plan.get('project_id') != project:
        return [], []
    state = json.loads(statepath.read_text()) if statepath else {}
    if state and (state.get('project_id') != project or state.get('revision') != plan.get('revision')):
        raise ValueError('制作计划与状态不匹配')
    events = {}
    for event in state.get('events', []):
        jid = event.get('job')
        events[jid] = max(events.get(jid, 0), stamp(event.get('at')))
    result = []
    for job in plan.get('jobs', []):
        jid = identifier(job['id'])
        live = state.get('jobs', {}).get(jid, {})
        status = live.get('status', 'pending')
        valid, reason = receipt_info(live)
        if status in ('submitted', 'running', 'succeeded', 'failed') and not valid:
            status = 'unverified'
        if status in ('submitted', 'running', 'succeeded') and not live.get('task_id'):
            status = 'unverified'
        delivery = live.get('delivery', {})
        updated = max(stamp(live.get('updated_at')), stamp(live.get('claimed_at')),
                      events.get(jid, 0), *(stamp(v.get('at')) for v in delivery.values() if isinstance(v, dict)))
        result.append({
            'id': hashlib.sha256((str(path) + '\0' + str(statepath) + '\0' + jid).encode()).hexdigest()[:24],
            'job': jid, 'kind': clean(job.get('kind', 'task'))[:40],
            'label': clean(job.get('name') or job.get('label') or jid)[:200],
            'plan': clean(path.parent.name)[:200], 'revision': clean(plan.get('revision', ''))[:200],
            'status': status, 'recordedStatus': live.get('status', 'pending'),
            'taskId': live.get('task_id'), 'receiptVerified': valid,
            'review': live.get('review', 'pending'), 'reviewNote': clean(live.get('review_note', '')),
            'delivery': list(delivery), 'requiredDelivery': job.get('delivery_channels', []),
            'reason': clean(live.get('error') or live.get('reason') or reason)[:500]
                      if status in ('failed', 'unknown', 'unverified') else '',
            'updatedAt': updated or stamp(state.get('created_at')) or path.stat().st_mtime,
            'stale': False,
        })
    dependencies = {r['path'] for live in state.get('jobs', {}).values()
                    for r in live.get('receipts', []) if isinstance(r, dict) and isinstance(r.get('path'), str)}
    return result, sorted(dependencies)


def read(store, data):
    project = identifier(data.get('project'))
    production_index.init(store)
    with store.connect() as c:
        row = c.execute('SELECT config FROM production_sources WHERE project=?', (project,)).fetchone()
        runs = [dict(r) for r in c.execute(
            'SELECT id,label,script,status,started,ended,heartbeat,exit_code FROM runs '
            'WHERE project=? ORDER BY started DESC LIMIT 20', (project,))]
    config = json.loads(row['config']) if row else {}
    warnings, jobs = [], []
    with LOCK:
        paths = production_index.plan_paths(config)
        cache = CACHE.setdefault((str(store.db), project), {})
        keys = set()
        for path, registered_state in sorted(paths.items()):
            statepath = registered_state or next((p for p in (
                path.with_name('production-state.json'), path.with_name('state.json')) if p.is_file()), None)
            key = (str(path), str(registered_state))
            keys.add(key)
            old = cache.get(key, {})
            try:
                if statepath is None and any(r['recordedStatus'] != 'pending' for r in old.get('rows', [])):
                    raise ValueError('已运行的状态文件暂不可读')
                signature = (fingerprint(path), fingerprint(statepath))
                unchanged = (old.get('signature') == signature and
                    all(fingerprint(p) == value for p, value in old.get('receipts', {}).items()))
                if unchanged:
                    rows = old['rows']
                else:
                    rows, dependencies = plan_jobs(path, statepath, project)
                    cache[key] = {'rows': rows, 'signature': signature,
                                  'receipts': {p: fingerprint(p) for p in dependencies}}
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                warnings.append('某份制作记录暂不可读或归属不匹配；上次状态待核对')
                rows = [{**r, 'stale': True} for r in old.get('rows', [])]
            jobs.extend(copy.deepcopy(rows))
        for key in list(cache):
            if key not in keys:
                del cache[key]
    now = time.time()
    for run in runs:
        if run['status'] == 'running' and now - run['heartbeat'] > 20:
            run['status'] = 'unknown'
    jobs.sort(key=lambda j: (j['status'] in ACTIVE and not j['stale'], j['updatedAt'], j['id']), reverse=True)
    summary = {
        'active': sum(j['status'] in ACTIVE and not j['stale'] for j in jobs),
        'review': sum(j['status'] == 'succeeded' and j['review'] == 'pending' and not j['stale'] for j in jobs),
        'attention': sum(j['stale'] or j['status'] in ('failed', 'unknown', 'unverified') or j['review'] == 'failed' for j in jobs),
        'scriptsRunning': sum(r['status'] == 'running' for r in runs),
    }
    result = {'project': project, 'jobs': jobs, 'runs': runs,
              'warnings': sorted(set(warnings)), 'summary': summary}
    result['revision'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
