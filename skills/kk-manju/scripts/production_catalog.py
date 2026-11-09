"""Explicit local production catalog. No canvas mutation or generation side effects."""
import base64
import copy
import json
import time
from pathlib import Path
from companion_store import identifier, text_field, sha


def init(store):
    with store.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS production_catalog(project TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL);
        CREATE TABLE IF NOT EXISTS production_choices(project TEXT, episode TEXT, version TEXT, shot TEXT,
          candidate TEXT, revision INTEGER, updated REAL, PRIMARY KEY(project,episode,version,shot));
        CREATE TABLE IF NOT EXISTS production_actions(id TEXT PRIMARY KEY, payload TEXT, result TEXT, created REAL);
        ''')


def import_catalog(store, data):
    project = identifier(data['project'])
    # Only a local CLI can register files. The HTTP API never accepts paths.
    seen=set()
    for episode in data['episodes']:
        identifier(episode['id'])
        for version in episode['versions']:
            identifier(version['id'])
            for shot in version['shots']:
                identifier(shot['id'])
                for candidate in shot['candidates']:
                    identifier(candidate['id'])
                    if candidate['id'] in seen: continue
                    seen.add(candidate['id'])
                    path=Path(candidate['path']).resolve()
                    if not path.is_file() or path.suffix.lower() not in ('.mp4','.webm','.mov'): raise ValueError('候选媒体不存在')
                    candidate['path']=str(path)
    init(store)
    with store.connect() as c:
        c.execute('INSERT OR REPLACE INTO production_catalog VALUES(?,?,?)',(project,json.dumps(data,ensure_ascii=False),time.time()))
    return {'project':project,'candidates':len(seen)}


def raw(store, project):
    init(store)
    with store.connect() as c:
        r=c.execute('SELECT data FROM production_catalog WHERE project=?',(project,)).fetchone()
    return json.loads(r['data']) if r else {'project':project,'episodes':[],'assets':[]}


def item(store, data):
    project=identifier(data.get('project'))
    import production_index
    production_index.refresh(store,project)
    catalog=raw(store,project)
    try:
        ep=next(x for x in catalog['episodes'] if x['id']==data.get('episode'))
        version=next(x for x in ep['versions'] if x['id']==data.get('version'))
        shot=next(x for x in version['shots'] if x['id']==data.get('shot'))
        candidate=next(x for x in shot['candidates'] if x['id']==data.get('candidate'))
    except StopIteration: raise ValueError('候选与当前剧集、版本或镜头不匹配')
    return catalog, shot, candidate


def thumb(path):
    if path and Path(path).is_file():
        return 'data:image/jpeg;base64,'+base64.b64encode(Path(path).read_bytes()).decode()
    return None


def read(store, data):
    import production_flow
    project=identifier(data.get('project'))
    import production_index
    production_index.refresh(store,project)
    catalog=raw(store, project)
    flows=production_flow.read(store,project)
    flow_index={f['id']:f for f in flows}
    with store.connect() as c:
        account=c.execute("SELECT value FROM libtv_sync_settings WHERE key='account'").fetchone() if c.execute("SELECT 1 FROM sqlite_master WHERE name='libtv_sync_settings'").fetchone() else None
        choices={(r['episode'],r['version'],r['shot']):dict(r) for r in c.execute('SELECT * FROM production_choices WHERE project=?',(project,))}
        issues=[dict(r) for r in c.execute('SELECT * FROM issues WHERE project=? ORDER BY created DESC LIMIT 200',(project,))]
        projects=[{'id':r['id'],'name':r['name']} for r in c.execute('SELECT id,name FROM projects ORDER BY updated DESC')]
        runs=[{k:r[k] for k in ('id','label','script','status','heartbeat','exit_code')} for r in c.execute('SELECT * FROM runs WHERE project=? ORDER BY started DESC LIMIT 20',(project,))]
        snapshot=c.execute('SELECT snapshot FROM projects WHERE id=?',(project,)).fetchone()
    for ep in catalog['episodes']:
        for ver in ep['versions']:
            for shot in ver['shots']:
                choice=choices.get((ep['id'],ver['id'],shot['id']))
                shot['adopted']=choice['candidate'] if choice else shot.get('adopted')
                shot['choiceRevision']=choice['revision'] if choice else 0
                for candidate in shot['candidates']:
                    flow=flow_index.get(candidate.get('flowId'))
                    if flow:
                        candidate['creative']=flow.get('review','pending')
                        candidate['reviewNote']=flow.get('reviewNote','')
                    candidate['poster']=thumb(candidate.pop('thumbnail',None))
                    if candidate.get('remote'):
                        candidate['remoteNode']=candidate['id']
                        url=candidate.pop('remoteUrl',None)
                        cached=store.directory/'libtv-cache'/(__import__('hashlib').sha256((url or '').encode()).hexdigest()+'.jpg')
                        candidate['poster']=thumb(cached)
                        candidate['available']=bool(url and account and account['value']==candidate.get('originAccount'))
                        candidate.pop('originAccount',None)
                    else:candidate['available']=Path(candidate.pop('path')).is_file()
    for a in catalog.get('assets',[]):
        a['poster']=thumb(a.pop('thumbnail',None));a.pop('path',None)
    for r in runs:
        if r['status']=='running' and time.time()-r['heartbeat']>20:r['status']='unknown'
    with store.connect() as c:
        library=[]
        for row in c.execute('SELECT data FROM production_catalog ORDER BY updated DESC'):
            d=json.loads(row['data']);cover=None
            try:
                shots=d['episodes'][0]['versions'][0]['shots']
                cover=thumb(shots[min(2,len(shots)-1)]['candidates'][0].get('thumbnail'))
            except (KeyError,IndexError):pass
            library.append({'project':d['project'],'title':d.get('title',d['project']),'episodes':len(d['episodes']),'poster':cover})
    if not data.get('includePending',False):
        # Existing installed panels assume every row has playable media. New panels
        # explicitly opt into planned/running rows; persistent catalog is unchanged.
        for ep in catalog['episodes']:
            for ver in ep['versions']:ver['shots']=[s for s in ver['shots'] if s['candidates']]
            ep['versions']=[v for v in ep['versions'] if v['shots']]
        catalog['episodes']=[e for e in catalog['episodes'] if e['versions']]
    revision=__import__('hashlib').sha256(json.dumps(catalog,sort_keys=True).encode()).hexdigest()
    return {**catalog,'catalogRevision':revision,'flows':flows,'library':library,'issues':issues,'projects':projects,'runs':runs,'batches':json.loads(snapshot['snapshot']).get('batches',[]) if snapshot else []}


def adopt(store, data):
    _,shot,candidate=item(store,data)
    if candidate.get('remote'):
        with store.connect() as c:account=c.execute("SELECT value FROM libtv_sync_settings WHERE key='account'").fetchone()
        if not account or account['value']!=candidate.get('originAccount') or not candidate.get('remoteUrl'):raise ValueError('当前账号无法确认此媒体版本')
    else:
        if not Path(candidate['path']).is_file():raise ValueError('媒体缺失，无法采用')
        if candidate.get('sha256') and sha(candidate['path'])!=candidate['sha256']:raise ValueError('媒体内容已改变，请重新导入核对')
    key=identifier(data.get('id'))
    payload={k:data.get(k) for k in ('project','episode','version','shot','candidate','expectedRevision')}
    encoded=json.dumps(payload,sort_keys=True)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT payload,result FROM production_actions WHERE id=?',(key,)).fetchone()
        if old:
            if old['payload']!=encoded:raise ValueError('重复请求内容不一致')
            return json.loads(old['result'])
        scope=tuple(data[k] for k in ('project','episode','version','shot'))
        old=c.execute('SELECT revision FROM production_choices WHERE project=? AND episode=? AND version=? AND shot=?',scope).fetchone()
        revision=old['revision'] if old else 0
        if type(data.get('expectedRevision')) is not int or data['expectedRevision']!=revision:raise ValueError('采用状态已被更新，请刷新后重试')
        result={'candidate':candidate['id'],'revision':revision+1,'scope':'local_production','canvasSync':'not_requested'}
        c.execute('INSERT OR REPLACE INTO production_choices VALUES(?,?,?,?,?,?,?)',(*scope,candidate['id'],revision+1,time.time()))
        c.execute('INSERT INTO production_actions VALUES(?,?,?,?)',(key,encoded,json.dumps(result),time.time()))
    store.event(data['project'],'adopt',f"{data['episode']} / {data['version']} / {data['shot']} / {candidate['id']} · 用户明确采用（本地制作记录）")
    return result


def media(store, project, key):
    catalog=raw(store,identifier(project))
    for ep in catalog['episodes']:
        for ver in ep['versions']:
            for shot in ver['shots']:
                for candidate in shot['candidates']:
                    if candidate['id']==key:
                        file=Path(candidate['path'])
                        if candidate.get('sha256') and sha(file)!=candidate['sha256']:raise ValueError('媒体内容已改变')
                        return file
    raise ValueError('未知媒体')
