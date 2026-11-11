"""Read-only LibTV tools through the user's authenticated local Codex App Server.

No token extraction, shell RPC, model turns, persistent chats or paid tools.
The ephemeral thread is only the MCP transport context required by App Server.
"""
import json
import queue
import shutil
import subprocess
import threading
import time
from companion_store import clean

READ_TOOLS=frozenset({'doctor','list_projects','read_project','get_project_snapshot',
                      'search_assets','list_asset_folders','get_asset'})


class CodexBridge:
    def __init__(self, cwd):
        self.cwd=str(cwd); self.lock=threading.RLock(); self.process=None
        self.pending={}; self.seq=0; self.thread_id=None; self.server=None
        self.status={'codex':'unchecked','libtv':'unchecked','checkedAt':None,'error':None}
        self.identity=None

    def _write(self, value):
        self.process.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');self.process.stdin.flush()

    def _reader(self, process):
        for line in process.stdout:
            try: message=json.loads(line)
            except ValueError: continue
            waiter=self.pending.get(message.get('id'))
            if process is not self.process:continue
            if ('result' in message or 'error' in message) and waiter:
                waiter.put(message)
            elif 'id' in message and 'method' in message:
                # This integration has no approval or elicitation delegation.
                # A read needing interaction fails visibly instead of auto-approving.
                try: self._write({'id':message['id'],'error':{'code':-32601,'message':'Read-only sync requires user interaction in Codex'}})
                except (OSError,ValueError,AttributeError): pass
        if process is self.process:
            for waiter in list(self.pending.values()):waiter.put({'error':{'message':'Codex connection closed'}})

    def _rpc(self, method, params, timeout=60):
        self.seq+=1; key=self.seq; waiter=queue.Queue();self.pending[key]=waiter
        try:
            self._write({'id':key,'method':method,'params':params})
            reply=waiter.get(timeout=timeout)
            if 'error' in reply:raise RuntimeError(str(reply['error'].get('message','连接调用失败'))[:500])
            return reply['result']
        except queue.Empty:raise TimeoutError('Codex 连接超时，请重新同步')
        finally:self.pending.pop(key,None)

    def close(self):
        with self.lock:
            p=self.process;self.process=None;self.thread_id=None;self.server=None
            if p and p.poll() is None:
                p.terminate()
                try:p.wait(timeout=4)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=4)
            if p:
                for stream in (p.stdin,p.stdout):
                    if stream:stream.close()

    def connect(self):
        with self.lock:
            if self.process and self.process.poll() is None and self.thread_id and self.server:return
            self.close()
            executable=shutil.which('codex')
            if not executable:raise RuntimeError('未找到本地 Codex，请安装或启动 Codex')
            self.status.update(codex='connecting',libtv='connecting',error=None)
            self.process=subprocess.Popen([executable,'app-server','--listen','stdio://'],cwd=self.cwd,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
            threading.Thread(target=self._reader,args=(self.process,),daemon=True).start()
            self._rpc('initialize',{'clientInfo':{'name':'kk_manju_companion','title':'KK 漫剧制作面板','version':'0.7.0'},'capabilities':{'experimentalApi':True}})
            self._write({'method':'initialized'})
            account=self._rpc('account/read',{'refreshToken':False})
            if not account.get('account'):raise RuntimeError('本地 Codex 尚未登录')
            self.status['codex']='connected'
            result=self._rpc('thread/start',{'ephemeral':True,'cwd':self.cwd})
            self.thread_id=result['thread']['id']
            # App Server may return a server before its MCP tools finish loading.
            # Recheck the same transport context instead of destroying startup
            # and beginning an identical, potentially incomplete handshake again.
            for attempt in range(3):
                inventory=self._rpc('mcpServerStatus/list',{'threadId':self.thread_id,'detail':'toolsAndAuthOnly','limit':100})
                matches=[s for s in inventory.get('data',[]) if s.get('name')=='libtv']
                if matches and READ_TOOLS.issubset(set(matches[0].get('tools',{}))):break
                if attempt<2:time.sleep(.5)
            if not matches:raise RuntimeError('本地 Codex 未启用 LibTV 插件')
            if not READ_TOOLS.issubset(set(matches[0].get('tools',{}))):raise RuntimeError('LibTV 读取工具暂未就绪，后台将重试；持续出现时请检查官方插件')
            self.server=matches[0]['name'];self.status['codex']='connected'

    def call(self, tool, arguments):
        if tool not in READ_TOOLS:raise ValueError('同步连接只允许登记的读取工具')
        with self.lock:
            try:
                self.connect()
                result=self._rpc('mcpServer/tool/call',{'threadId':self.thread_id,'server':self.server,'tool':tool,'arguments':arguments})
                if result.get('isError'):raise RuntimeError('LibTV 读取失败，请检查登录及权限')
                data=result.get('structuredContent')
                if data is None:
                    for part in result.get('content',[]):
                        if part.get('type')=='text':
                            try:data=json.loads(part['text']);break
                            except ValueError:pass
                if not isinstance(data,dict):raise RuntimeError('LibTV 未返回可识别的读取结果')
                if data.get('ok') is False:raise RuntimeError(str(data.get('message') or data.get('code') or 'LibTV 读取被拒绝')[:300])
                if tool=='doctor':
                    if data.get('status')!='ready':raise RuntimeError('LibTV 连接未就绪')
                    identity=data.get('identity',{})
                    self.identity={'userId':identity.get('userId'),'accountId':identity.get('activeAccountId')}
                    if not all(self.identity.values()):raise RuntimeError('LibTV 未返回明确账号身份')
                    self.status.update(codex='connected',libtv='connected',checkedAt=time.time(),error=None)
                return data
            except Exception as e:
                self.status.update(libtv='error',error=clean(str(e))[:300],checkedAt=time.time())
                if not self.process or self.process.poll() is not None or self.status['codex']!='connected':self.status['codex']='error'
                if not self.server or isinstance(e,(TimeoutError,OSError)):
                    self.close()
                raise

    def check(self):
        self.call('doctor',{})
        return dict(self.status)
