#!/usr/bin/env python3
"""KK Manju companion: read-only local dashboard, browser inbox, tracked CLI runner."""
import argparse
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from companion_store import SCRIPTS, VERSION, Store, clean, identifier, sha
from extension_release import installed_release
import production_catalog
from libtv_sync import SyncService

DEFAULT_STATE = Path.home() / '.local/share/kk-manju-companion'
DEFAULT_SKILL = Path.home() / '.codex/skills/kk-manju'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def token_for(store):
    path = store.directory / 'pairing-token'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, 'w') as f:
        token = secrets.token_urlsafe(32)
        f.write(token)
    return token


def handler_factory(store, skill, web, token):
    sync = SyncService(store, skill)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # No request bodies, query strings or pairing secrets in server logs.
            pass

        def origin_allowed(self):
            origin = self.headers.get('Origin', '')
            return not origin or origin == f'http://127.0.0.1:{self.server.server_port}' or bool(__import__('re').fullmatch(r'chrome-extension://[a-p]{32}', origin))

        def host_allowed(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def send(self, status, data, mime='application/json; charset=utf-8'):
            if isinstance(data, (dict, list)):
                data = json.dumps(data, ensure_ascii=False).encode()
            if isinstance(data, str):
                data = data.encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            origin = self.headers.get('Origin')
            if origin and self.origin_allowed():
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            return self.host_allowed() and self.origin_allowed() and hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)

        def do_OPTIONS(self):
            self.send(204 if self.host_allowed() and self.origin_allowed() else 403, b'')

        def do_GET(self):
            if not self.host_allowed() or not self.origin_allowed():
                return self.send(403, {'error': '不允许的来源'})
            path = urlsplit(self.path)
            if path.path == '/health':
                return self.send(200, {'service': 'kk-manju-companion', 'version': VERSION,
                                       'instance': sha(store.directory / 'pairing-token')[:16]})
            if path.path == '/api/state':
                if not self.authorized():
                    return self.send(401, {'error': '请先配对本地面板'})
                q = parse_qs(path.query)
                try:
                    project = identifier(q['project'][0]) if q.get('project') else None
                    state = store.state(skill, project, max(0, int(q.get('after', ['0'])[0])))
                    state['extensionRelease'] = installed_release(store.directory)
                    return self.send(200, state)
                except ValueError as e:
                    return self.send(400, {'error': str(e)})
            if path.path == '/api/extension-release':
                if not self.authorized(): return self.send(401, {'error': '请先配对'})
                return self.send(200, installed_release(store.directory))
            if path.path in ('/api/libtv/thumbnail','/api/libtv/media'):
                if not self.authorized():return self.send(401,{'error':'未配对'})
                q=parse_qs(path.query)
                try:
                    file,mime=sync.cached_media(q.get('project',[''])[0],q.get('id',[''])[0],path.path.endswith('thumbnail'))
                    return self.send(200,file.read_bytes(),mime)
                except (ValueError,OSError,subprocess.SubprocessError):return self.send(404,{'error':'此素材暂不可预览，请同步后重试'})
            if path.path == '/api/production/media':
                if not self.authorized(): return self.send(401, {'error':'未配对'})
                q=parse_qs(path.query)
                try:
                    if q.get('id',[''])[0].startswith('r-'):
                        file,mime=sync.cached_media(q.get('project',[''])[0],q['id'][0])
                        return self.send(200,file.read_bytes(),mime)
                    file=production_catalog.media(store,q.get('project',[''])[0],q.get('id',[''])[0])
                    if not file.is_file(): return self.send(404, {'error':'媒体文件已移动'})
                    self.send_response(200)
                    self.send_header('Content-Type','video/mp4')
                    self.send_header('Content-Length',str(file.stat().st_size))
                    self.send_header('Cache-Control','private, no-store')
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.end_headers()
                    with file.open('rb') as f:
                        while chunk:=f.read(256*1024): self.wfile.write(chunk)
                    return
                except (ValueError,OSError): return self.send(404, {'error':'未知或不可用媒体'})
            files = {'/production': ('production.html','text/html; charset=utf-8'),
                     '/production.js': ('production.js','text/javascript; charset=utf-8'),
                     '/production.css': ('production.css','text/css; charset=utf-8'),
                     '/': ('index.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8'),
                     '/logo.svg': ('logo.svg', 'image/svg+xml')}
            if path.path not in files:
                return self.send(404, {'error': '不存在'})
            file, mime = files[path.path]
            body = (web / file).read_text()
            if file in ('index.html','production.html'):
                body = body.replace('__LOCAL_TOKEN__', token)
            self.send(200, body, mime)

        def do_POST(self):
            if not self.authorized():
                return self.send(401, {'error': '未配对或来源不允许'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536 or self.headers.get_content_type() != 'application/json':
                    return self.send(400, {'error': '需要有界 JSON 请求'})
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求必须是 JSON 对象')
                routes = {'/api/production': lambda d: production_catalog.read(store,d),
                          '/api/connections': lambda d: sync.connections(identifier(d.get('project')),d.get('force') is True,auto=True),
                          '/api/libtv/sync': lambda d: sync.start(identifier(d.get('project')),d.get('full') is True),
                          '/api/libtv/catalog': lambda d: sync.read(identifier(d.get('project'))),
                          '/api/production/adopt': lambda d: production_catalog.adopt(store,d),
                          '/api/context': store.context, '/api/issues': store.issue,
                          '/api/batches': store.batches, '/api/commands': store.command, '/api/commands/observed': store.command_observed}
                fn = routes.get(self.path)
                if not fn:
                    return self.send(404, {'error': '无此操作；本地面板不提供运行脚本接口'})
                self.send(200, fn(data))
            except (ValueError, TypeError, KeyError) as e:
                self.send(400, {'error': str(e)})

    Handler.sync_service = sync
    return Handler


def run_script(store, skill, args):
    script = skill / 'scripts' / (args.script + '.py')
    if args.script not in SCRIPTS or not script.is_file() or script.resolve().parent != (skill / 'scripts').resolve():
        raise ValueError('只可运行 Skill 内登记的固定脚本')
    params = args.arguments
    if params[:1] == ['--']:
        params = params[1:]
    key = store.start_run(args.project, args.script, args.label or SCRIPTS[args.script][0], sha(script))
    print(json.dumps({'run': key, 'status': 'running'}, ensure_ascii=False), flush=True)
    stop = threading.Event()
    def beat():
        while not stop.wait(3):
            store.heartbeat(key)
    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    process, code, artifacts = None, 1, []
    try:
        executable = [sys.executable]
        if SCRIPTS[args.script][2]:
            try:
                __import__('PIL')
            except ImportError:
                import shutil
                uv = shutil.which('uv')
                if not uv:
                    raise RuntimeError('该脚本需要 Pillow；请使用安装 Pillow 的 Python 或安装 uv')
                executable = [uv, 'run', '--no-project', '--with', 'pillow', 'python']
        process = subprocess.Popen(executable + [str(script)] + params, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                   start_new_session=True, bufsize=1)
        for line in iter(lambda: process.stdout.readline(4000), ''):
            store.event(args.project, 'log', line.rstrip(), key)
            print(clean(line.rstrip()), flush=True)
        process.stdout.close()
        code = process.wait()
        if code == 0:
            for raw in args.artifact:
                path = Path(raw).expanduser().resolve()
                if not path.is_file():
                    raise ValueError('声明的产物未找到：' + str(path))
                artifacts.append({'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path), 'review': 'pending'})
    except BaseException as e:
        code = 130 if isinstance(e, KeyboardInterrupt) else 1
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        store.event(args.project, 'error', str(e) or type(e).__name__, key)
        print(clean(str(e)), file=sys.stderr)
    finally:
        if process and process.stdout:
            process.stdout.close()
        stop.set()
        thread.join(timeout=4)
        store.finish_run(key, code, artifacts)
    return code


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    p.add_argument('--skill-root', type=Path, default=DEFAULT_SKILL)
    commands = p.add_subparsers(dest='command', required=True)
    for verb in ('serve', 'start'):
        a = commands.add_parser(verb)
        a.add_argument('--port', type=int, default=18765)
        a.add_argument('--web-root', type=Path)
    a = commands.add_parser('run')
    a.add_argument('--project', required=True)
    a.add_argument('--script', choices=list(SCRIPTS), required=True)
    a.add_argument('--label')
    a.add_argument('--artifact', action='append', default=[])
    a.add_argument('arguments', nargs=argparse.REMAINDER)
    a = commands.add_parser('project')
    a.add_argument('--id', required=True); a.add_argument('--name', required=True)
    a.add_argument('--snapshot', type=Path)
    a = commands.add_parser('issue'); a.add_argument('file', type=Path)
    a = commands.add_parser('state'); a.add_argument('--project')
    a = commands.add_parser('pending'); a.add_argument('--project', required=True)
    a = commands.add_parser('receipt'); a.add_argument('--id', required=True); a.add_argument('file', type=Path)
    args = p.parse_args()
    store = Store(args.state_dir)
    skill = args.skill_root.expanduser().resolve()
    if args.command == 'run':
        return run_script(store, skill, args)
    if args.command == 'project':
        store.project(args.id, args.name, read_json(args.snapshot) if args.snapshot else None)
    elif args.command == 'issue':
        print(json.dumps(store.issue(read_json(args.file)), ensure_ascii=False))
    elif args.command == 'receipt':
        store.finish_command(args.id, read_json(args.file))
    elif args.command in ('state', 'pending'):
        state = store.state(skill, args.project)
        if args.command == 'pending':
            state = [c for c in state['commands'] if c['status'] != 'verified']
        print(json.dumps(state, ensure_ascii=False, indent=2))
    elif args.command in ('start', 'serve'):
        web = args.web_root or (skill / 'assets/companion/web')
        if not (web / 'index.html').is_file():
            web = Path(__file__).resolve().parent.parent / 'web'
        if not (web / 'index.html').is_file():
            raise ValueError('未安装面板静态文件')
        token = token_for(store)
        url = f'http://127.0.0.1:{args.port}'
        if args.command == 'serve':
            server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_factory(store, skill, web, token))
            server.daemon_threads = True
            print(url, flush=True)
            def stop(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, stop)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.RequestHandlerClass.sync_service.bridge.close()
                server.server_close()
        else:
            instance = sha(store.directory / 'pairing-token')[:16]
            def existing():
                try:
                    with urllib.request.urlopen(url + '/health', timeout=.7) as res:
                        found = json.load(res)
                    if found.get('service') != 'kk-manju-companion' or found.get('instance') != instance or found.get('version') != VERSION:
                        raise ValueError('端口被其他服务或其他数据目录占用')
                    return True
                except (OSError, urllib.error.URLError):
                    return False
            if not existing():
                with (store.directory / 'server.log').open('a') as log:
                    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--state-dir', str(store.directory),
                        '--skill-root', str(skill), 'serve', '--port', str(args.port), '--web-root', str(web.resolve())],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                for _ in range(30):
                    if existing():
                        break
                    if child.poll() is not None:
                        raise RuntimeError('面板启动失败，查看本地 server.log')
                    time.sleep(.1)
                else:
                    raise RuntimeError('面板未就绪')
            print(json.dumps({'url': url, 'version': VERSION, 'stateDirectory': str(store.directory), 'status': 'ready'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        print(clean(str(error)), file=sys.stderr)
        sys.exit(2)
