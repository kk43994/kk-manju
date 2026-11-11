"""Bounded CapCut draft tools through the authenticated Codex App Server.

No cookies, token extraction, model turns or generation/charge confirmation.
This client is local CLI infrastructure, never an arbitrary HTTP tool relay.
"""
import json
import shutil
import subprocess
import threading
from codex_bridge import CodexBridge
from companion_store import clean

TOOLS = frozenset({'capcut_get_profile', 'start_stdraft_session', 'check_stdraft_state',
                   'get_stdraft_snapshot', 'push_stdraft_snapshot', 'search_capcut_material'})


class CapCutBridge(CodexBridge):
    def connect(self):
        with self.lock:
            if self.process and self.process.poll() is None and self.server:
                return
            self.close()
            executable = shutil.which('codex')
            if not executable:
                raise RuntimeError('未找到本地 Codex')
            self.process = subprocess.Popen([executable, 'app-server', '--listen', 'stdio://'],
                cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
            threading.Thread(target=self._reader, args=(self.process,), daemon=True).start()
            self._rpc('initialize', {'clientInfo': {'name': 'kk_capcut_edit', 'version': '0.8.0'},
                                    'capabilities': {'experimentalApi': True}})
            self._write({'method': 'initialized'})
            if not self._rpc('account/read', {'refreshToken': False}).get('account'):
                raise RuntimeError('本地 Codex 尚未登录')
            self.thread_id = self._rpc('thread/start', {'ephemeral': True, 'cwd': self.cwd})['thread']['id']
            inventory = self._rpc('mcpServerStatus/list', {'threadId': self.thread_id,
                'detail': 'toolsAndAuthOnly', 'limit': 100}, timeout=120)
            servers = [s for s in inventory.get('data', []) if s['name'] == 'capcut_creation']
            if not servers or not TOOLS.issubset(servers[0].get('tools', {})):
                raise RuntimeError('请先启用并授权官方 CapCut 插件')
            self.server = servers[0]['name']

    def call(self, tool, arguments):
        if tool not in TOOLS:
            raise ValueError('此连接仅提供 CapCut 工程与素材工具')
        with self.lock:
            self.connect()
            result = self._rpc('mcpServer/tool/call', {'threadId': self.thread_id,
                'server': self.server, 'tool': tool, 'arguments': arguments}, timeout=180)
            if result.get('isError'):
                message = ' '.join(p.get('text', '') for p in result.get('content', []) if p.get('type') == 'text')
                raise RuntimeError('CapCut 工具调用失败：' + clean(message)[:300])
            data = result.get('structuredContent')
            if data is None:
                for part in result.get('content', []):
                    if part.get('type') == 'text':
                        try:
                            data = json.loads(part['text'])
                            break
                        except ValueError:
                            pass
            if not isinstance(data, dict):
                raise RuntimeError('CapCut 没有返回可识别的工程回执')
            return data
