"""Read-only episode discovery from explicit LibTV labels and registered plans.

Only the local CLI may register a production root. No generation, source-plan
writes, review decisions or adoption happen during indexing.
"""
import argparse,copy,hashlib,json,re,threading,time
from pathlib import Path
from companion_store import Store,identifier

LOCK=threading.RLock()

def init(store):
    with store.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS production_sources(project TEXT PRIMARY KEY,config TEXT NOT NULL)')

def register(store,project,root,inherit=None):
    init(store);root=Path(root).resolve()
    if not root.is_dir():raise ValueError('制作目录不存在')
    with store.connect() as c:
        old=c.execute('SELECT config FROM production_sources WHERE project=?',(identifier(project),)).fetchone()
        config=json.loads(old['config']) if old else {}
        config.update(root=str(root),inherit=inherit if inherit is not None else config.get('inherit',[]))
        c.execute('INSERT OR REPLACE INTO production_sources VALUES(?,?)',(project,json.dumps(config,ensure_ascii=False)))
    return config

def register_plan(store,project,path,state=None):
    """Trusted local runner hook: exact files only, never widen to a parent tree."""
    project=identifier(project);path=Path(path).expanduser().resolve()
    plan=json.loads(path.read_text())
    if plan.get('project_id')!=project:raise ValueError('制作计划与运行项目不匹配')
    init(store)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT config FROM production_sources WHERE project=?',(project,)).fetchone()
        config=json.loads(row['config']) if row else {}
        plans={p['path']:p for p in config.get('plans',[])}
        entry=plans.get(str(path),{'path':str(path)})
        if state:entry['state']=str(Path(state).expanduser().resolve())
        plans[str(path)]=entry;config['plans']=list(plans.values())
        c.execute('INSERT OR REPLACE INTO production_sources VALUES(?,?)',(project,json.dumps(config,ensure_ascii=False)))

def episode_id(text):
    ep=re.search(r'(?<![A-Za-z0-9])EP0*(\d+)(?!\d)',text,re.I)
    if ep:return f'EP{int(ep[1]):03d}'
    ep=re.search(r'第\s*(\d+|[零〇一二两三四五六七八九十百]+)\s*集',text)
    if ep:
        value=ep[1]
        if value.isdigit():n=int(value)
        else:
            digits={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};n=0;digit=0
            for char in value:
                if char in ('十','百'):n+=(digit or 1)*({'十':10,'百':100}[char]);digit=0
                else:digit=digits[char]
            n+=digit
        return f'EP{n:03d}' if n>0 else None
    ep=re.search(r'(?<![A-Za-z0-9])E(\d+)V\d+',text,re.I)
    return f'EP{int(ep[1]):03d}' if ep else None

def scope(text,context=''):
    ep=episode_id(text);parent=episode_id(context)
    if ep and parent and ep!=parent:context=''
    ep=ep or parent
    version=re.search(r'(?:^|[\s｜|·-])V(\d+)(?!\d)',text,re.I)
    # A bare V05 node label is a shot when the group supplies its version.
    group_version=re.search(r'(?:^|[\s｜|·-])V(\d+)(?!\d)',context,re.I)
    if not re.search(r'EP\d+|第.+集|E\d+V',text,re.I):version=group_version
    version=version or group_version
    return (ep,f'V{int(version[1])}') if ep and version else None

def shot_id(text,episode):
    m=re.search(r'(?<![A-Za-z0-9])E(\d+)V(\d+)(?!\d)',text,re.I)
    if m:return f'E{int(m[1])}V{int(m[2]):02d}' if int(m[1])==int(episode[2:]) else None
    m=re.search(r'(?:^|[\s｜|·])V(\d+)(?!\d)',text,re.I)
    if not m:return None
    return (f'E{int(episode[2:])}' if int(episode[2:])>1 else '')+f'V{int(m[1]):02d}'

def number(value):
    try:return float(value) if float(value)>0 else 0
    except (ValueError,TypeError):return 0

def remote_candidate(m,account):
    if m.get('type')!='video' or not m.get('urls') or m.get('taskStatus')!=2:return None
    identity=json.dumps([m['id'],m.get('resourceIds',[]),m['urls']],sort_keys=True)
    return {'id':'r-'+hashlib.sha256(identity.encode()).hexdigest()[:24],
        'node':m['id'],'remote':True,'remoteUrl':m['urls'][0],'originAccount':account,
        'resourceIds':m.get('resourceIds',[]),'label':m['name'],'duration':number(m.get('duration')),
        'platform':'succeeded','technical':'pending','creative':'pending','reviewNote':'从 LibTV 同步，尚无审核结论',
        'source':'libtv_index','taskId':m.get('taskId'),
        'createdAt':number(str(m.get('taskId',''))[:14]) or number(m.get('updatedAt'))}

def plan_paths(config):
    paths={}
    if not config:return paths
    if config.get('root'):
        root=Path(config['root']).resolve()
        paths={p.resolve():None for p in root.rglob('plan.json') if p.resolve().is_relative_to(root)}
    paths.update({Path(p['path']).resolve():Path(p['state']).resolve() if p.get('state') else None for p in config.get('plans',[])})
    return paths

def plan_data(config,project):
    jobs=[];reviews={};errors=[]
    paths=plan_paths(config)
    for path,registered_state in sorted(paths.items()):
        try:
            plan=json.loads(path.read_text());where=scope(plan.get('revision',''))
            if plan.get('project_id')!=project or not where:continue
            statepath=registered_state or next((p for p in (path.with_name('production-state.json'),path.with_name('state.json')) if p.is_file()),None)
            state=json.loads(statepath.read_text()) if statepath else {}
            if state and (state.get('project_id')!=project or state.get('revision')!=plan['revision']):
                errors.append('某份制作计划与状态不匹配，已保留原记录');continue
            for job in plan.get('jobs',[]):
                if job.get('kind')!='video':continue
                sid=shot_id(job['id'],where[0])
                if not sid:continue
                live=state.get('jobs',{}).get(job['id'],{})
                title=job.get('name') or (job.get('shots') or [{}])[0].get('purpose',sid).split('，')[0]
                status=live.get('status','pending')
                jobs.append((where,sid,title,number(job.get('duration_seconds')),status,live.get('task_id')))
                for output in live.get('outputs',[]):
                    if output.get('node_id'):reviews[output['node_id']]={'creative':live.get('review','pending'),
                        'reviewNote':live.get('review_note',''),'taskId':live.get('task_id')}
        except (OSError,ValueError,KeyError,TypeError):errors.append('某份制作记录正在更新，暂时沿用上次结果')
    return jobs,reviews,errors

def refresh(store,project):
    import production_catalog as pc
    init(store);pc.init(store)
    with LOCK:
        with store.connect() as c:
            source=c.execute('SELECT config FROM production_sources WHERE project=?',(project,)).fetchone()
            account=c.execute("SELECT value FROM libtv_sync_settings WHERE key='account'").fetchone() if c.execute("SELECT 1 FROM sqlite_master WHERE name='libtv_sync_settings'").fetchone() else None
            snapshot=c.execute('SELECT data FROM libtv_snapshots WHERE account=? AND project=?',(account['value'],project)).fetchone() if account else None
        config=json.loads(source['config']) if source else None
        jobs,reviews,errors=plan_data(config,project)
        graph=json.loads(snapshot['data']) if snapshot else {}
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT data FROM production_catalog WHERE project=?',(project,)).fetchone()
            catalog=json.loads(row['data']) if row else {'project':project,'episodes':[],'assets':[]}
            before=json.dumps(catalog,sort_keys=True,ensure_ascii=False)
            def ensure(where,sid,title=None,duration=0):
                eid,vid=where
                ep=next((e for e in catalog['episodes'] if e['id']==eid),None)
                if ep is None:
                    ep={'id':eid,'name':f'第 {int(eid[2:])} 集','versions':[],'source':'production_index'};catalog['episodes'].append(ep)
                ver=next((v for v in ep['versions'] if v['id']==vid),None)
                if ver is None:
                    ver={'id':vid,'name':'制作版本','shots':[],'source':'production_index'};ep['versions'].append(ver)
                shot=next((s for s in ver['shots'] if s['id']==sid),None)
                if shot is None:
                    shot={'id':sid,'title':title or sid,'candidates':[],'adopted':None,'source':'production_index'};ver['shots'].append(shot)
                if shot.get('source')=='production_index':
                    if title:shot['title']=title
                    if duration:shot['plannedDuration']=duration
                return shot
            for board in graph.get('storyboards',[]):
                where=scope(board.get('name',''))
                if not where:continue
                for item in board.get('rows',[]):
                    sid=shot_id(item.get('id',''),where[0])
                    if sid:ensure(where,sid,duration=number(item.get('durationSeconds')))
            for where,sid,title,duration,status,task in jobs:
                shot=ensure(where,sid,title,duration)
                if shot.get('source')=='production_index':shot['jobStatus']=status;shot['activeTaskId']=task
            for media in graph.get('media',[]):
                if media.get('type')!='video':continue
                where=scope(media.get('name',''),media.get('group',''))
                if not where:continue
                # Only the explicit shot token is read; prompts/references never determine ownership.
                label=media['name'];sid=shot_id(re.sub(r'(?:\bEP\d+|第\s*[\d零〇一二两三四五六七八九十百]+\s*集)[｜|\s-]+V\d+(?:-R\d+)?','',label,flags=re.I),where[0])
                if not sid:continue
                shot=ensure(where,sid)
                candidate=remote_candidate(media,account['value'])
                if shot.get('source')=='production_index':
                    active=shot.get('jobStatus') in ('claimed','submitted','running','unknown')
                    if shot.get('activeTaskId') and media.get('taskId')==shot['activeTaskId']:active=False
                    if not candidate or not active:
                        shot['jobStatus']='succeeded' if candidate else 'running' if media.get('taskStatus') in (0,1) else 'failed' if media.get('failedReason') else 'unknown'
                if not candidate:continue
                # Existing manually registered media remain the owning candidate.
                old=next((x for x in shot['candidates'] if x['id']==candidate['id'] or (x.get('node')==candidate['node'] and not x.get('remote'))),None)
                if old and not old.get('remote'):continue
                candidate.update(reviews.get(candidate['node'],{}))
                if old:old.update(candidate)
                else:shot['candidates'].append(candidate)
            # Explicit project-local reuse, registered from the actual director plan.
            for reuse in (config or {}).get('inherit',[]):
                ep=next((e for e in catalog['episodes'] if e['id']==reuse['episode']),None)
                base=next((v for v in (ep or {}).get('versions',[]) if v['id']==reuse['fromVersion']),None)
                origin=next((s for s in (base or {}).get('shots',[]) if s['id']==reuse['shot']),None)
                if origin:
                    shot=ensure((reuse['episode'],reuse['version']),reuse['shot'],origin['title'],origin.get('plannedDuration',0))
                    if shot.get('source')=='production_index':
                        shot['candidates']=copy.deepcopy(origin['candidates']);shot['jobStatus']=origin.get('jobStatus','pending');shot['reuseNote']=reuse.get('reason','沿用已有镜头')
            for ep in catalog['episodes']:
                if ep.get('source')!='production_index':continue
                ep['versions'].sort(key=lambda v:int(v['id'][1:]),reverse=True)
                for ver in ep['versions']:
                    # Some official labels abbreviate E2V05 as V05. Reconcile
                    # existing auto-indexed aliases without dropping any choice.
                    for alias in list(ver['shots']):
                        if int(ep['id'][2:])<=1 or not re.fullmatch(r'V\d+',alias['id']):continue
                        picked=c.execute('SELECT 1 FROM production_choices WHERE project=? AND episode=? AND version=? AND shot=?',(project,ep['id'],ver['id'],alias['id'])).fetchone()
                        if picked:continue
                        canonical=f'E{int(ep["id"][2:])}'+alias['id']
                        target=next((s for s in ver['shots'] if s['id']==canonical),None)
                        if target:
                            known={x['id'] for x in target['candidates']}
                            target['candidates'].extend(x for x in alias['candidates'] if x['id'] not in known)
                            ver['shots'].remove(alias)
                        else:alias['id']=canonical
                    ver['shots'].sort(key=lambda s:int(re.search(r'(\d+)$',s['id'])[1]))
                    for shot in ver['shots']:shot['candidates'].sort(key=lambda x:(x.get('createdAt',0),x['id']))
            catalog['episodes'].sort(key=lambda e:e['id'])
            catalog['indexWarnings']=sorted(set(errors))
            after=json.dumps(catalog,sort_keys=True,ensure_ascii=False)
            if before!=after and (row or catalog['episodes']):c.execute('INSERT OR REPLACE INTO production_catalog VALUES(?,?,?)',(project,after,time.time()))

def main():
    p=argparse.ArgumentParser();p.add_argument('--state-dir',type=Path,default=Path.home()/'.local/share/kk-manju-companion')
    p.add_argument('--project',required=True);p.add_argument('--root',required=True);p.add_argument('--inherit',type=Path)
    a=p.parse_args();store=Store(a.state_dir)
    register(store,a.project,a.root,json.loads(a.inherit.read_text()) if a.inherit else None);refresh(store,a.project)
    print(json.dumps({'project':a.project,'registered':True}))

if __name__=='__main__':main()
