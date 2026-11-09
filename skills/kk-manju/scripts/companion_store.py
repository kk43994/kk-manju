"""Local, append-only execution evidence for KK Manju. No platform credentials."""
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

VERSION = '0.6.0'
SCRIPTS = {
    'production_index': ('集数目录同步', '读取已登记制作目录与平台快照，不生成、不修改制作计划', False),
    'production_flow': ('制作闭环同步', '连接真实生成、检查、审核和返修记录，不代替创作者采用', False),
    'preserve_audio': ('恢复原声音轨', '同长视频原画面与原声包合并，校验指纹且保留原件', False),
    'extension_release': ('扩展本机发布', '备份、包完整性校验与自动更新版本发布', False),
    'canvas_batches': ('批次分组校验', '分组计划、节点归属、素材和连线回读', False),
    'asset_inventory': ('资产盘点', '文件指纹与重复项，不自动采用', False),
    'pipeline': ('制作队列', '依赖、领取、回执与交付', False),
    'production_contract': ('分镜契约', '站位、镜序、转场与提示词编译', False),
    'prompt_guard': ('提示词检查', '按已核对的模型限制检查长度', False),
    'media_probe': ('媒体检查与抽帧', '实际媒体规格和指定时间截图', False),
    'export_verify': ('导出校验', '完整解码、帧数和时间戳', False),
    'speech_review': ('对白定位', '转写辅助定位，不能代替试听', False),
    'audio_patch': ('声音局部修复', '同声源片段替换，输出待审', False),
    'render_episode': ('镜头合片', '依据已审剪辑单合成', False),
    'render_split_edit': ('声画分离剪辑', 'J/L-cut、转场余量、语句保护', False),
    'subtitle_layer': ('独立字幕层', '已校对台词、字体、原声包校验', True),
    'picture_coverage': ('补镜覆盖', '等长换画面，保留音轨', False),
    'image_compare': ('图片对比', '等宽预览与原件指纹', True),
    'delivery_save': ('交付保存', '原件保存、哈希及解码检查', False),
    'upload_parts': ('上传分片', '沿用既有票据及恢复身份', False),
}


def clean(value):
    text = str(value)
    text = re.sub(r'(?i)(Bearer\s+)[\w.\-]+', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)((?:token|password|secret|api[_-]?key|authorization)\s*[=:]\s*)[^\s,;]+', r'\1[已隐藏]', text)
    text = re.sub(r'https?://[^\s<>"\']+', lambda m: m[0].split('?')[0].split('#')[0], text)
    return text[:4000]


def text_field(value, name, limit=200, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{name} 无效')
    return value.strip()


def identifier(value, name='ID'):
    value = text_field(value, name, 120)
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]+', value):
        raise ValueError(f'{name} 格式无效')
    return value


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.directory / 'events.sqlite3'
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT, updated REAL, snapshot TEXT);
            CREATE TABLE IF NOT EXISTS contexts(client TEXT PRIMARY KEY, project TEXT, updated REAL, data TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project TEXT, script TEXT, label TEXT,
                status TEXT, started REAL, ended REAL, heartbeat REAL, pid INTEGER, exit_code INTEGER,
                script_hash TEXT, artifacts TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT, project TEXT,
                time REAL, kind TEXT, message TEXT);
            CREATE TABLE IF NOT EXISTS issues(id TEXT PRIMARY KEY, project TEXT, node TEXT, timecode REAL,
                category TEXT, description TEXT, created REAL, status TEXT, basis TEXT);
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, project TEXT, kind TEXT, created REAL,
                status TEXT, context TEXT, receipt TEXT);
            ''')
        os.chmod(self.db, 0o600)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def project(self, project, name=None, snapshot=None):
        project = identifier(project, '项目 ID')
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO projects VALUES(?,?,?,?)',
                      (project, name or project, time.time(), '{}'))
            if name:
                c.execute('UPDATE projects SET name=?,updated=? WHERE id=?',
                          (text_field(name, '项目名'), time.time(), project))
            if snapshot is not None:
                c.execute('UPDATE projects SET snapshot=?,updated=? WHERE id=?',
                          (json.dumps(snapshot, ensure_ascii=False), time.time(), project))

    def event(self, project, kind, message, run=None):
        with self.connect() as c:
            c.execute('INSERT INTO events(run,project,time,kind,message) VALUES(?,?,?,?,?)',
                      (run, project, time.time(), kind, clean(message)))
            if run:
                # Bounded stdout history per execution; terminal events remain.
                c.execute("DELETE FROM events WHERE run=? AND kind='log' AND seq NOT IN "
                          "(SELECT seq FROM events WHERE run=? AND kind='log' ORDER BY seq DESC LIMIT 1500)", (run, run))

    def context(self, data):
        project = identifier(data.get('project'), '项目 ID')
        client = identifier(data.get('client'), '浏览器实例')
        url = urlsplit(data.get('url', ''))
        if url.scheme != 'https' or url.hostname not in ('www.liblib.tv', 'liblib.tv') or url.path != '/canvas':
            raise ValueError('只接受 LibTV 画布上下文')
        from urllib.parse import parse_qs
        if parse_qs(url.query).get('projectId') != [project]:
            raise ValueError('画布 URL 与项目 ID 不一致')
        selected = data.get('selected', [])
        if not isinstance(selected, list) or len(selected) > 500:
            raise ValueError('选择项过多')
        selected = [identifier(x, '节点 ID') for x in selected]
        value = {'project': project, 'name': text_field(data.get('name') or project, '项目名'),
                 'client': client, 'url': f'https://www.liblib.tv/canvas?projectId={project}',
                 'selected': selected, 'arrangeAvailable': data.get('arrangeAvailable') is True,
                 'basis': 'browser_dom', 'observedAt': time.time()}
        extension = data.get('extension')
        if extension is not None:
            if (not isinstance(extension, dict) or
                    not re.fullmatch(r'\d+(\.\d+){0,3}', str(extension.get('version', ''))) or
                    not re.fullmatch(r'[a-f0-9]{64}', str(extension.get('buildId', '')))):
                raise ValueError('扩展版本信息无效')
            value['extension'] = {k: extension[k] for k in ('version', 'buildId')}
        self.project(project, value['name'])
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO contexts VALUES(?,?,?,?)',
                      (client, project, time.time(), json.dumps(value, ensure_ascii=False)))
        return value

    def issue(self, data, basis='user_report'):
        project = identifier(data.get('project'), '项目 ID')
        key = identifier(data.get('id') or str(uuid.uuid4()))
        node = identifier(data['node'], '节点 ID') if data.get('node') else ''
        tc = data.get('timecode')
        if tc is not None and (isinstance(tc, bool) or not isinstance(tc, (int, float)) or not math.isfinite(tc) or not 0 <= tc <= 86400):
            raise ValueError('时间必须是有效秒数')
        category = text_field(data.get('category', '其他'), '问题类型', 40)
        desc = text_field(data.get('description'), '问题说明', 2000)
        self.project(project)
        with self.connect() as c:
            old = c.execute('SELECT * FROM issues WHERE id=?', (key,)).fetchone()
            if old:
                if (old['project'], old['node'], old['timecode'], old['category'], old['description']) != (project, node, tc, category, desc):
                    raise ValueError('同一请求 ID 内容不一致')
                return dict(old)
            c.execute('INSERT INTO issues VALUES(?,?,?,?,?,?,?,?,?)',
                      (key, project, node, tc, category, desc, time.time(), 'open', basis))
        self.event(project, 'issue', desc)
        return {'id': key, 'status': 'open'}

    def command(self, data):
        project = identifier(data.get('project'), '项目 ID')
        key = identifier(data.get('id'), '请求 ID')
        kind = data.get('kind')
        if kind not in ('native_arrange', 'native_group_layout', 'sync_snapshot', 'organize_selection', 'organize_batches'):
            raise ValueError('操作不在支持范围内')
        client = identifier(data.get('client'), '浏览器实例')
        with self.connect() as c:
            row = c.execute('SELECT * FROM contexts WHERE client=?', (client,)).fetchone()
            if not row or row['project'] != project or time.time() - row['updated'] > 30:
                raise ValueError('画布上下文已过期，请先同步')
            context = json.loads(row['data'])
            if kind == 'organize_selection' and not context['selected']:
                raise ValueError('请先选择待整理节点')
            batch = text_field(data.get('batch', ''), '批次名称', 100, required=False)
            if kind == 'organize_selection' and not batch:
                raise ValueError('请填写批次名称')
            context['batch'] = batch
            if data.get('selected', context['selected']) != context['selected']:
                raise ValueError('选择项已变化，请重试')
            old = c.execute('SELECT * FROM commands WHERE id=?', (key,)).fetchone()
            if old:
                previous = json.loads(old['context'])
                if old['project'] != project or old['kind'] != kind or previous['selected'] != context['selected'] or previous.get('batch', '') != batch:
                    raise ValueError('重复请求内容发生变化')
                return dict(old)
            status = 'awaiting_readback' if kind in ('native_arrange', 'native_group_layout') else 'needs_assistant'
            c.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?)',
                      (key, project, kind, time.time(), status, json.dumps(context, ensure_ascii=False), '{}'))
        self.event(project, 'request', f'{kind} / {status}')
        return {'id': key, 'status': status}

    def command_observed(self, data):
        key = identifier(data.get('id'))
        note = text_field(data.get('note'), '观察说明', 500)
        with self.connect() as c:
            row = c.execute('SELECT * FROM commands WHERE id=?', (key,)).fetchone()
            if not row or row['kind'] not in ('native_arrange', 'native_group_layout'):
                raise ValueError('未知原生操作')
            project = identifier(data.get('project'), '项目 ID')
            client = identifier(data.get('client'), '浏览器实例')
            context = json.loads(row['context'])
            if row['project'] != project or context['client'] != client:
                raise ValueError('观察回执与原始项目或浏览器实例不一致')
            # DOM observation is not a persisted-platform receipt.
            if row['status'] == 'awaiting_readback':
                c.execute('UPDATE commands SET receipt=? WHERE id=?',
                          (json.dumps({'domObservation': note}, ensure_ascii=False), key))
        return {'id': key, 'status': row['status']}

    def batches(self, data):
        project = identifier(data.get('project'), '项目 ID')
        client = identifier(data.get('client'), '浏览器实例')
        with self.connect() as c:
            ctx = c.execute('SELECT * FROM contexts WHERE client=?', (client,)).fetchone()
            if not ctx or ctx['project'] != project or time.time() - ctx['updated'] > 30:
                raise ValueError('请先同步当前画布')
            row = c.execute('SELECT snapshot FROM projects WHERE id=?', (project,)).fetchone()
        snap = json.loads(row['snapshot']) if row else {}
        return {'project': project, 'observedAt': snap.get('observedAt'),
                'batches': snap.get('batches', [])}

    def finish_command(self, key, receipt):
        if receipt.get('status', 'verified') not in ('verified', 'superseded'):
            raise ValueError('回执状态不支持')
        if not receipt.get('project') or not receipt.get('tool') or not receipt.get('readback') or not receipt.get('evidence'):
            raise ValueError('必须保存项目、工具、回读结果及证据位置')
        with self.connect() as c:
            row = c.execute('SELECT * FROM commands WHERE id=?', (key,)).fetchone()
            if not row or row['project'] != receipt['project']:
                raise ValueError('回执项目不匹配')
            c.execute('UPDATE commands SET status=?,receipt=? WHERE id=?',
                      (receipt.get('status', 'verified'), json.dumps(receipt, ensure_ascii=False), key))

    def start_run(self, project, script, label, script_hash):
        self.project(project)
        key, now = str(uuid.uuid4()), time.time()
        with self.connect() as c:
            c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (key, project, script, label, 'running', now, None, now, os.getpid(), None, script_hash, '[]'))
        self.event(project, 'started', f'{label} · {script}.py', key)
        return key

    def heartbeat(self, key):
        with self.connect() as c:
            c.execute("UPDATE runs SET heartbeat=? WHERE id=? AND status='running'", (time.time(), key))

    def finish_run(self, key, code, artifacts):
        with self.connect() as c:
            c.execute('UPDATE runs SET status=?,exit_code=?,ended=?,artifacts=? WHERE id=?',
                      ('completed' if code == 0 else 'failed', code, time.time(), json.dumps(artifacts, ensure_ascii=False), key))
            row = c.execute('SELECT project,label FROM runs WHERE id=?', (key,)).fetchone()
        self.event(row['project'], 'completed' if code == 0 else 'failed', f"{row['label']} · 退出码 {code}", key)

    def state(self, skill_root, project=None, after=0):
        with self.connect() as c:
            projects = [dict(x) for x in c.execute('SELECT * FROM projects ORDER BY updated DESC')]
            for p in projects:
                p['snapshot'] = json.loads(p['snapshot'])
            selected = project or (projects[0]['id'] if projects else '')
            contexts = [json.loads(x['data']) for x in c.execute('SELECT * FROM contexts WHERE project=? ORDER BY updated DESC', (selected,))]
            runs = [dict(x) for x in c.execute('SELECT * FROM runs WHERE project=? ORDER BY started DESC LIMIT 100', (selected,))]
            for r in runs:
                r['artifacts'] = json.loads(r['artifacts'])
                if r['status'] == 'running' and time.time() - r['heartbeat'] > 20:
                    r['status'] = 'unknown'
            issues = [dict(x) for x in c.execute('SELECT * FROM issues WHERE project=? ORDER BY created DESC LIMIT 200', (selected,))]
            commands = [dict(x) for x in c.execute('SELECT * FROM commands WHERE project=? ORDER BY created DESC LIMIT 100', (selected,))]
            events = [dict(x) for x in c.execute('SELECT * FROM events WHERE project=? AND seq>? ORDER BY seq DESC LIMIT 160', (selected, after))][::-1]
        catalog = [{'id': k, 'name': v[0], 'description': v[1], 'available': (Path(skill_root) / 'scripts' / (k + '.py')).is_file(),
                    'running': sum(r['script'] == k and r['status'] == 'running' for r in runs)} for k, v in SCRIPTS.items()]
        return {'version': VERSION, 'serverTime': time.time(), 'projects': projects, 'project': selected,
                'contexts': contexts, 'runs': runs, 'issues': issues, 'commands': commands, 'events': events,
                'scripts': catalog, 'stateDirectory': str(self.directory)}
