"""Atomic remote catalog snapshots, separate from creative choices and local edits."""
import hashlib,json,mimetypes,threading,time,subprocess,urllib.request,urllib.parse
from pathlib import Path
from codex_bridge import CodexBridge
from companion_store import identifier,clean

DIRECTORY_CACHE_SECONDS=300
ASSET_DETAIL_CACHE_SECONDS=86400


def init(store):
    with store.connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS libtv_library(account TEXT PRIMARY KEY,data TEXT,updated REAL);
          CREATE TABLE IF NOT EXISTS libtv_snapshots(account TEXT,project TEXT,data TEXT,updated REAL,PRIMARY KEY(account,project));
          CREATE TABLE IF NOT EXISTS libtv_sync_runs(id TEXT PRIMARY KEY,project TEXT,status TEXT,phase TEXT,error TEXT,started REAL,ended REAL);
          CREATE TABLE IF NOT EXISTS libtv_sync_settings(key TEXT PRIMARY KEY,value TEXT);''')
        c.execute("UPDATE libtv_sync_runs SET status='interrupted',error='服务重启，同步未完成；保留上次成功快照',ended=? WHERE status='running'",(time.time(),))


def decode(row):return json.loads(row['data']) if row else None


class SyncService:
    def __init__(self,store,cwd):
        self.store=store;init(store);self.bridge=CodexBridge(cwd);self.lock=threading.Lock();self.checking=False
        self.media_lock=threading.Semaphore(3)
        self.cache_locks=[threading.Lock() for _ in range(64)]
        self.check_lock=threading.Lock()

    def account(self):
        with self.store.connect() as c:
            r=c.execute("SELECT value FROM libtv_sync_settings WHERE key='account'").fetchone()
        return r['value'] if r else None

    def _set_account(self):
        identity=self.bridge.identity;account=str(identity['userId'])+':'+str(identity['accountId'])
        with self.store.connect() as c:c.execute('INSERT OR REPLACE INTO libtv_sync_settings VALUES(?,?)',('account',account))
        return account

    def ensure_auto(self,project):
        with self.store.connect() as c:
            latest=c.execute('SELECT status,started FROM libtv_sync_runs WHERE project=? ORDER BY started DESC LIMIT 1',(project,)).fetchone()
        if not latest or (latest['status']!='running' and time.time()-latest['started']>=30):self.start(project)

    def connections(self,project,force=False,auto=False):
        if auto:self.ensure_auto(project)
        with self.store.connect() as c:
            browser=c.execute('SELECT project,updated FROM contexts ORDER BY updated DESC LIMIT 1').fetchone()
            latest=c.execute('SELECT id,status,phase,error,started,ended FROM libtv_sync_runs WHERE project=? ORDER BY started DESC LIMIT 1',(project,)).fetchone()
            last=c.execute('SELECT updated FROM libtv_snapshots WHERE project=? AND account=?',(project,self.account())).fetchone()
        status=dict(self.bridge.status)
        fresh=status.get('checkedAt') and time.time()-status['checkedAt']<90
        if (force or not fresh) and self.check_lock.acquire(blocking=False):
            self.checking=True
            def check():
                try:self.bridge.check();self._set_account()
                except Exception:pass
                finally:self.checking=False;self.check_lock.release()
            threading.Thread(target=check,daemon=True).start()
        return {'browser':{'status':'connected' if browser and time.time()-browser['updated']<30 else 'disconnected',
                           'currentProject':browser['project'] if browser else None,'projectMatches':bool(browser and browser['project']==project)},
                'codex':{'status':status['codex'] if fresh and not force else 'connecting'},
                'libtv':{'status':status['libtv'] if fresh and not force else 'connecting'},'error':status.get('error'),
                'sync':dict(latest) if latest else None,'lastSuccess':last['updated'] if last else None,
                'transport':'codex_app_server_official_mcp','automaticExecution':False}

    def start(self,project,full=False):
        project=identifier(project)
        if not self.lock.acquire(blocking=False):
            return {'status':'running','message':'已有同步进行中，请等待当前同步完成'}
        key='sync-'+__import__('uuid').uuid4().hex
        with self.store.connect() as c:c.execute('INSERT INTO libtv_sync_runs VALUES(?,?,?,?,?,?,?)',(key,project,'running','正在连接 Codex 与 LibTV',None,time.time(),None))
        threading.Thread(target=self._run,args=(key,project,full),daemon=True).start()
        return {'id':key,'status':'running'}

    def phase(self,key,text):
        with self.store.connect() as c:c.execute('UPDATE libtv_sync_runs SET phase=? WHERE id=?',(text,key))

    def pages(self,tool,args,extract,key,label):
        rows=[];seen=set()
        for page in range(1,101):
            self.phase(key,f'{label} · 第 {page} 页')
            data=self.bridge.call(tool,{**args,'page':page,'pageSize':100})
            items,total=extract(data)
            if not isinstance(items,list) or not isinstance(total,int):raise ValueError('分页结果缺少项目或总数')
            for item in items:
                identity=item.get('uuid',item.get('assetId',item.get('id')))
                if identity is None:raise ValueError('分页项目缺少身份，未替换上次快照')
                if identity in seen:raise ValueError('分页返回重复项目，请重新同步')
                seen.add(identity);rows.append(item)
            if len(rows)>total:raise ValueError('分页总数发生变化，请重新同步')
            if len(rows)==total:return rows
            if not items:raise ValueError('分页提前结束，未覆盖全部目录')
        raise ValueError('目录超过同步上限，未替换上次快照')

    def _run(self,key,project,full=False):
        started=time.monotonic()
        try:
            self.bridge.check();account=self._set_account()
            cached,_=self.raw(project);now=time.time()
            projects_checked=cached.get('projectsCheckedAt',cached.get('syncedAt',0))
            projects_cached=not full and now-projects_checked<DIRECTORY_CACHE_SECONDS and project in {p['uuid'] for p in cached['projects']}
            if projects_cached:projects=cached['projects']
            else:
                projects=self.pages('list_projects',{},lambda d:(d['data']['projectMetaList'],d['data']['total']),key,'核对项目目录')
                projects_checked=time.time()
            if project not in {p['uuid'] for p in projects}:raise ValueError('当前项目不在已授权账号中，未更新本地数据')
            self.phase(key,'更新当前画布 · 已有素材继续可用')
            graph=self.bridge.call('read_project',{'projectId':project,'view':'nodes'})
            if graph.get('projectId')!=project:raise ValueError('返回项目不匹配')
            groups={n['id']:n.get('data',{}).get('name','') for n in graph['nodes'] if n.get('type')=='group'}
            media=[]
            for node in graph['nodes']:
                d=node.get('data',{});kind=d.get('type') or node.get('type')
                if kind=='group':continue
                urls=d.get('url',[]);urls=[urls] if isinstance(urls,str) else urls
                urls=[u for u in (urls or []) if isinstance(u,str)]
                media.append({'id':node['id'],'name':d.get('name') or node['id'],'type':kind,'urls':urls,
                              'group':groups.get(node.get('parentId'),''),'resourceIds':d.get('resourceIds',[]),
                              'taskStatus':d.get('taskInfo',{}).get('status'),'taskId':d.get('taskInfo',{}).get('taskId'),
                              'failedReason':d.get('taskInfo',{}).get('failedReason'),'updatedAt':d.get('_updatedAtMs',0),
                              'duration':next((x.get('durationSec') for x in node.get('resourceMeta',{}).get('items',[]) if x.get('kind')=='video'),None),
                              'source':'canvas'})
            assets=self.pages('search_assets',{'scope':'personal','projectId':project},lambda d:(d['items'],d['total']),key,'读取个人资产库')
            folders_checked=cached.get('foldersCheckedAt',cached.get('syncedAt',0))
            if not full and now-folders_checked<DIRECTORY_CACHE_SECONDS:folders=cached['folders']
            else:
                folders=self.pages('list_asset_folders',{'scope':'personal','projectId':project},lambda d:(d['folders'],d['total']),key,'核对资产目录')
                folders_checked=time.time()
            # Official assetVersion identifies changes. Unchanged details and media
            # survive normal sync; full refresh also recovers expired preview URLs.
            previous={a['assetId']:a for a in cached['assets']};reused=0;refreshed=0
            for i,asset in enumerate(assets):
                old=previous.get(asset['assetId'],{})
                detail_checked=old.get('detailCheckedAt',cached.get('syncedAt',0))
                if (not full and asset.get('assetVersion') and asset['assetVersion']==old.get('assetVersion')
                    and now-detail_checked<ASSET_DETAIL_CACHE_SECONDS and isinstance(old.get('items'),list)):
                    asset['items']=old['items'];asset['detailCheckedAt']=detail_checked;reused+=1
                    continue
                self.phase(key,f'更新变化的资产 · {refreshed+1} 项')
                detail=self.bridge.call('get_asset',{'scope':'personal','projectId':project,'assetId':asset['assetId']})['asset']
                if detail.get('assetId')!=asset['assetId']:raise ValueError('资产详情身份不匹配')
                asset['items']=detail.get('items',[]);asset['detailCheckedAt']=time.time();refreshed+=1
            self.bridge.check()
            if account!=str(self.bridge.identity['userId'])+':'+str(self.bridge.identity['accountId']):
                self._set_account()
                raise ValueError('同步过程中账号改变，未提交快照')
            now=time.time();stats={'mode':'full' if full else 'incremental','elapsedSeconds':round(time.monotonic()-started,2),
                'projectsReused':projects_cached,'assetsReused':reused,'assetsRefreshed':refreshed}
            boards=[{'id':n['id'],'name':n.get('data',{}).get('name',''),
                'rows':[{k:r.get(k) for k in ('id','durationSeconds')} for r in n.get('data',{}).get('rows',[])]}
                for n in graph['nodes'] if n.get('type')=='script-v2']
            snapshot={'project':project,'revision':graph.get('revision'),'media':media,'groups':groups,'storyboards':boards,'syncedAt':now,'syncStats':stats}
            library={'projects':[{k:p.get(k) for k in ('uuid','name','projectSpaceId','updatedAtMs')} for p in projects],
                     'assets':assets,'folders':folders,'syncedAt':now,
                     'projectsCheckedAt':projects_checked,'foldersCheckedAt':folders_checked}
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT OR REPLACE INTO libtv_library VALUES(?,?,?)',(account,json.dumps(library,ensure_ascii=False),now))
                c.execute('INSERT OR REPLACE INTO libtv_snapshots VALUES(?,?,?,?)',(account,project,json.dumps(snapshot,ensure_ascii=False),now))
                c.execute("UPDATE libtv_sync_runs SET status='completed',phase=?,ended=? WHERE id=?",(f"{stats['elapsedSeconds']:g} 秒 · 画布 {len(media)} 项 · 资产复用 {reused} / 更新 {refreshed}",now,key))
            self.store.event(project,'sync',f'LibTV 同步完成：{len(projects)} 个项目、当前画布 {len(media)} 项、{len(assets)} 个个人资产')
        except Exception as e:
            with self.store.connect() as c:c.execute("UPDATE libtv_sync_runs SET status='failed',error=?,ended=? WHERE id=?",(clean(str(e))[:500],time.time(),key))
        finally:self.lock.release()

    def raw(self,project):
        account=self.account()
        with self.store.connect() as c:
            library=decode(c.execute('SELECT data FROM libtv_library WHERE account=?',(account,)).fetchone()) or {'projects':[],'assets':[],'folders':[]}
            snapshot=decode(c.execute('SELECT data FROM libtv_snapshots WHERE account=? AND project=?',(account,project)).fetchone()) or {'media':[],'groups':{}}
        return library,snapshot

    def read(self,project):
        library,snapshot=self.raw(project)
        def safe(item):return {k:v for k,v in item.items() if k not in ('urls','mediaUrls')}
        assets=[]
        for asset in library['assets']:
            for item in asset.get('items') or [{}]:
                assets.append({'id':'asset:'+asset['assetId']+':'+str(item.get('index',0)), 'name':asset['name'],
                               'type':item.get('type','unknown'),'folderId':str(asset.get('folderId','')),'source':'asset_library',
                               'hasMedia':bool(item.get('mediaUrls'))})
        return {'projects':library['projects'],'assets':assets,'assetCount':len(library['assets']),'folders':library['folders'],
                'media':[{**safe(m),'hasMedia':bool(m.get('urls'))} for m in snapshot['media']],
                'revision':snapshot.get('revision'),'syncedAt':snapshot.get('syncedAt'),'syncStats':snapshot.get('syncStats')}

    def source(self,project,key):
        library,snapshot=self.raw(project)
        if key.startswith('r-'):
            import production_catalog
            for ep in production_catalog.raw(self.store,project)['episodes']:
                for version in ep['versions']:
                    for shot in version['shots']:
                        for candidate in shot['candidates']:
                            if candidate['id']==key and candidate.get('remote') and candidate.get('originAccount')==self.account():
                                return candidate['remoteUrl'],'video'
            raise ValueError('此视频不属于当前账号的制作目录')
        if key.startswith('asset:'):
            for a in library['assets']:
                for item in a.get('items',[]):
                    if key=='asset:'+a['assetId']+':'+str(item.get('index',0)):
                        urls=item.get('mediaUrls',[]);return (urls[0],item.get('type')) if urls else (None,None)
        for item in snapshot['media']:
            if item['id']==key:return (item['urls'][0],item['type']) if item['urls'] else (None,None)
        raise ValueError('素材不属于已同步的当前项目或资产库')

    def cached_media(self,project,key,thumbnail=False):
        url,kind=self.source(identifier(project),identifier(key))
        parsed=urllib.parse.urlsplit(url or '')
        if parsed.scheme!='https' or parsed.hostname!='libtv-res.liblib.art' or parsed.username or parsed.password:
            raise ValueError('此素材尚无可用的官方媒体地址')
        digest=hashlib.sha256(url.encode()).hexdigest();cache=self.store.directory/'libtv-cache';cache.mkdir(exist_ok=True)
        if thumbnail:
            if kind not in ('image','video'):raise ValueError('此素材没有图片预览')
            target=cache/(digest+'.jpg')
            with self.cache_locks[int(digest[:8],16)%64],self.media_lock:
                if not target.exists():
                    result=subprocess.run(['ffmpeg','-v','error','-protocol_whitelist','https,tls,tcp,http,crypto','-i',url,'-frames:v','1','-vf','scale=480:-2','-f','image2pipe','-vcodec','mjpeg','-'],capture_output=True,timeout=25)
                    if result.returncode or not result.stdout:raise ValueError('官方媒体预览暂不可用')
                    tmp=target.with_suffix('.tmp');tmp.write_bytes(result.stdout);tmp.replace(target)
            return target,'image/jpeg'
        target=cache/(digest+('.mp4' if kind=='video' else '.mp3' if kind=='audio' else '.image'))
        with self.cache_locks[int(digest[:8],16)%64],self.media_lock:
            if not target.exists():
                with urllib.request.urlopen(url,timeout=30) as response:
                    if urllib.parse.urlsplit(response.url).hostname!='libtv-res.liblib.art':raise ValueError('媒体地址发生非预期跳转')
                    body=response.read(200*1024*1024+1)
                if len(body)>200*1024*1024:raise ValueError('素材超过本地预览上限，请在 LibTV 播放')
                tmp=target.with_suffix('.tmp');tmp.write_bytes(body);tmp.replace(target)
        mime=mimetypes.guess_type(parsed.path)[0]
        if not mime or mime.split('/')[0]!=kind:
            mime={'video':'video/mp4','audio':'audio/mpeg','image':'image/png'}.get(kind,'application/octet-stream')
        return target,mime
