"""Bind the existing pipeline.py ledger to production UI; never dispatch or adopt.

Only the trusted CLI registers file paths and verified deliveries. The browser reads
live evidence. Generation is executed by the active host through the LibTV plugin.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from companion_store import Store, identifier, sha
import production_catalog


def init(store):
    production_catalog.init(store)
    with store.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS production_flows(id TEXT PRIMARY KEY, project TEXT NOT NULL, config TEXT NOT NULL)')


def load(path):
    return json.loads(Path(path).read_text())


def register(store, config):
    init(store)
    for key in ('id', 'project', 'episode', 'version', 'shot', 'job'):
        identifier(config[key])
    plan = Path(config['plan']).resolve()
    state = Path(config['state']).resolve()
    p, s = load(plan), load(state)
    if p['project_id'] != config['project'] or s['project_id'] != config['project']:
        raise ValueError('管线项目不匹配')
    if p['revision'] != s['revision'] or config['job'] not in s['jobs']:
        raise ValueError('管线修订或镜头不匹配')
    catalog = production_catalog.raw(store, config['project'])
    find_shot(catalog, config)
    config = {**config, 'plan': str(plan), 'state': str(state), 'planSha256': sha(plan)}
    encoded = json.dumps(config, sort_keys=True, ensure_ascii=False)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        previous = c.execute('SELECT config FROM production_flows WHERE id=?', (config['id'],)).fetchone()
        if previous and previous['config'] != encoded:
            raise ValueError('管线身份已登记，不能替换输入；新修订请另建记录')
        c.execute('INSERT OR IGNORE INTO production_flows VALUES(?,?,?)', (config['id'], config['project'], encoded))
    return {'id': config['id'], 'registered': True}


def find_shot(catalog, config):
    try:
        ep = next(x for x in catalog['episodes'] if x['id'] == config['episode'])
        ver = next(x for x in ep['versions'] if x['id'] == config['version'])
        return next(x for x in ver['shots'] if x['id'] == config['shot'])
    except StopIteration:
        raise ValueError('剧集、版本或镜头未登记')


def configurations(store, project):
    init(store)
    with store.connect() as c:
        return [json.loads(r['config']) for r in c.execute('SELECT config FROM production_flows WHERE project=? ORDER BY rowid', (project,))]


def snapshot(config):
    s = load(config['state'])
    if s['project_id'] != config['project']:
        raise ValueError('状态项目不匹配')
    j = s['jobs'][config['job']]
    receipts = j.get('receipts', [])
    valid = bool(receipts) and all(Path(r['path']).is_file() and sha(r['path']) == r['sha256'] for r in receipts)
    # A changed plan blocks new dispatch; accepted tasks remain recoverable.
    return j, valid, sha(config['plan']) != config['planSha256']


def read(store, project):
    result = []
    for cfg in configurations(store, project):
        item = {k: cfg.get(k) for k in ('id', 'episode', 'version', 'shot', 'job', 'label', 'repairOf', 'reason')}
        try:
            j, valid, changed = snapshot(cfg)
            status = j['status']
            if status in ('submitted', 'running', 'succeeded', 'failed') and (not valid or not j.get('task_id')):
                status = 'unverified'
            item.update(status=status, taskId=j.get('task_id'), requestKey=j.get('request_key'),
                        taskKind='local_edit' if str(j.get('task_id','')).startswith('local:') else 'generation',
                        review=j.get('review', 'pending'), reviewBy=j.get('review_by'),
                        reviewNote=j.get('review_note', ''), delivery=list(j.get('delivery', {})),
                        inputChanged=changed, receiptVerified=valid,
                        updated=j.get('updated_at', j.get('claimed_at')))
        except (ValueError, OSError, KeyError, TypeError):
            item.update(status='unverified', review='pending', reviewNote='原始执行证据不可用，需恢复核对')
        result.append(item)
    return result


def ingest(store, flow_id, manifest_file, report_file):
    """Register only a hash-verified official delivery with complete decode evidence."""
    init(store)
    with store.connect() as c:
        row = c.execute('SELECT config FROM production_flows WHERE id=?', (identifier(flow_id),)).fetchone()
    if not row:
        raise ValueError('未知管线')
    cfg = json.loads(row['config'])
    job, receipts_valid, _ = snapshot(cfg)
    if job['status'] != 'succeeded' or not receipts_valid or not job.get('task_id'):
        raise ValueError('尚无可验证的成功生成回执')
    manifest, report = load(manifest_file), load(report_file)
    outputs = job.get('outputs', [])
    nodes = {o.get('node_id') for o in outputs}
    files = [f for f in manifest.get('files', []) if f.get('nodeId') in nodes]
    if len(files) != 1:
        raise ValueError('交付清单未唯一匹配本任务输出')
    source = Path(files[0]['path']).resolve()
    digest = sha(source)
    if files[0].get('sha256') != digest:
        raise ValueError('交付媒体指纹不匹配')
    if report.get('technical_status') != 'passed' or report.get('sha256') != digest or report.get('full_audio_video_decode_passed') is not True:
        raise ValueError('完整解码报告未通过或不属于此原件')
    key = 'm-' + digest[:24]
    poster = store.directory / 'posters' / (key + '.jpg')
    poster.parent.mkdir(exist_ok=True)
    if not poster.exists():
        subprocess.run(['ffmpeg', '-v', 'error', '-ss', '1', '-i', str(source), '-frames:v', '1', '-vf', 'scale=640:-2', '-y', str(poster)], check=True, capture_output=True)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(source)]))
    candidate = {'id': key, 'sha256': digest, 'path': str(source), 'thumbnail': str(poster),
                 'label': cfg['label'], 'node': files[0]['nodeId'], 'duration': float(probe['format']['duration']),
                 'platform': 'succeeded', 'technical': 'decode_passed', 'creative': job.get('review', 'pending'),
                 'reviewNote': job.get('review_note', ''), 'flowId': cfg['id'], 'source': 'verified_pipeline_delivery'}
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT data FROM production_catalog WHERE project=?', (cfg['project'],)).fetchone()
        catalog = json.loads(row['data'])
        shot = find_shot(catalog, cfg)
        old = next((x for x in shot['candidates'] if x['id'] == key), None)
        if old:
            if old.get('flowId') != cfg['id']:
                raise ValueError('同一原件已经归属其他候选；不可改写历史')
            old.update(candidate)
        else:
            shot['candidates'].append(candidate)
        c.execute('UPDATE production_catalog SET data=?,updated=? WHERE project=?', (json.dumps(catalog, ensure_ascii=False), time.time(), cfg['project']))
    return {'flow': cfg['id'], 'candidate': key, 'adopted': False}


def finish(store, flow_id, delivery_file, skill):
    """Recoverable, fixed post-generation chain. No media generation or auto-review."""
    init(store)
    with store.connect() as c:
        row=c.execute('SELECT config FROM production_flows WHERE id=?',(identifier(flow_id),)).fetchone()
    if not row: raise ValueError('未知管线')
    cfg=json.loads(row['config']); job, valid, _=snapshot(cfg)
    if job['status']!='succeeded' or not valid: raise ValueError('等待真实生成成功回执')
    directory=Path(cfg['state']).parent
    media=directory/'media'; review=directory/'review'; review.mkdir(exist_ok=True)
    def run(script, label, arguments):
        subprocess.run([sys.executable,str(skill/'scripts/companion.py'),'--state-dir',str(store.directory),
                        '--skill-root',str(skill),'run','--project',cfg['project'],'--script',script,'--label',label,'--',*map(str,arguments)],check=True)
    # delivery_save has its own manifest identity and atomic resume checks.
    run('delivery_save', cfg['label']+' · 保存正式交付', [delivery_file,'--output',media])
    manifest=media/'delivery-local.json'; files=load(manifest)['files']
    nodes={o.get('node_id') for o in job.get('outputs',[])}
    selected=[f for f in files if f.get('nodeId') in nodes]
    if len(selected)!=1: raise ValueError('交付未唯一匹配原任务')
    source=Path(selected[0]['path']); digest=sha(source)
    report=review/('decode-'+digest[:16]+'.json')
    if not report.exists():run('export_verify',cfg['label']+' · 完整声画解码',[source,'--report',report])
    evidence=load(report)
    if evidence.get('sha256')!=digest or evidence.get('technical_status')!='passed':raise ValueError('媒体检查未通过，保留报告等待处理')
    frames=review/('frames-'+digest[:16])
    if not (frames/'media-review.json').exists():run('media_probe',cfg['label']+' · 生成审查帧',[source,'--frames',frames])
    return ingest(store,flow_id,manifest,report)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=Path.home()/'.local/share/kk-manju-companion')
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('register'); a.add_argument('config', type=Path)
    a = sub.add_parser('read'); a.add_argument('project')
    a = sub.add_parser('ingest'); a.add_argument('flow'); a.add_argument('--manifest', required=True); a.add_argument('--report', required=True)
    a = sub.add_parser('finish'); a.add_argument('flow'); a.add_argument('--delivery', required=True); a.add_argument('--skill-root',type=Path,default=Path.home()/'.codex/skills/kk-manju')
    args = p.parse_args(); store = Store(args.state_dir)
    if args.command == 'register': result = register(store, load(args.config))
    elif args.command == 'read': result = read(store, identifier(args.project))
    elif args.command == 'finish': result = finish(store,args.flow,args.delivery,args.skill_root.resolve())
    else: result = ingest(store, args.flow, args.manifest, args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
