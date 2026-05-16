#!/usr/bin/env python3
"""
极简 HTTP Server —— 对外 serve 父母基金报告 index.html
- 只 serve 仓库根目录下的 index.html（以及 favicon 等静态文件）
- 禁止目录浏览、禁止访问 server/、.git/ 等敏感目录
- 设置合理的缓存头（总是刷新，避免父母看到旧数据）
- /api/refresh 端点：手动触发从邮箱拉取最新净值
"""
import os
import re
import json
import email
import socket
import logging
import tempfile
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote
from email.header import decode_header
import datetime as _dt

_cfg = Path(__file__).resolve().parent / 'config.env'
if _cfg.exists():
    for _ln in _cfg.read_text(encoding='utf-8').splitlines():
        _ln = _ln.strip()
        if not _ln or _ln.startswith('#') or '=' not in _ln:
            continue
        _k, _v = _ln.split('=', 1)
        _k = _k.strip()
        _v = _v.strip()
        if len(_v) >= 2 and _v[0] == _v[-1] and _v[0] in ('"', "'"):
            _v = _v[1:-1]
        os.environ.setdefault(_k, _v)

HOST = os.environ.get('WEB_HOST', '0.0.0.0')
PORT = int(os.environ.get('WEB_PORT', '8080') or '8080')
REPO_PATH = Path(os.environ.get('REPO_PATH', Path(__file__).resolve().parent.parent))
HTML_PATH = REPO_PATH / 'index.html'

IMAP_SERVER = os.environ.get('IMAP_SERVER', 'imap.163.com')
IMAP_PORT = int(os.environ.get('IMAP_PORT', '993') or '993')
IMAP_USER = os.environ.get('IMAP_USER', '')
IMAP_PASSWORD = os.environ.get('IMAP_PASSWORD', '').replace(' ', '')

PROXY_HOST = os.environ.get('IMAP_PROXY_HOST', '') or ''
PROXY_PORT = int(os.environ.get('IMAP_PROXY_PORT', '0') or '0')
PROXY_USERNAME = os.environ.get('IMAP_PROXY_USERNAME', '') or ''
PROXY_PASSWORD = os.environ.get('IMAP_PROXY_PASSWORD', '') or ''

ALLOWED = {
    '/': 'index.html',
    '/index.html': 'index.html',
    '/favicon.ico': 'favicon.ico',
    '/robots.txt': 'robots.txt',
}

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.ico':  'image/x-icon',
    '.txt':  'text/plain; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.svg':  'image/svg+xml',
    '.png':  'image/png',
}

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [WEB] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
log = logging.getLogger('web')


def _decode_subject(raw):
    if not raw:
        return ''
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8', errors='replace')
        except Exception:
            raw = str(raw)
    parts = decode_header(raw)
    result = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                result.append(text.decode(charset or 'utf-8', 'replace'))
            except LookupError:
                result.append(text.decode('utf-8', 'replace'))
        else:
            result.append(text)
    return ''.join(result)


def _extract_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get('Content-Disposition') or '')
            if ctype == 'text/plain' and 'attachment' not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    try:
                        return payload.decode(charset, 'replace')
                    except LookupError:
                        return payload.decode('utf-8', 'replace')
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            try:
                return payload.decode(charset, 'replace')
            except LookupError:
                return payload.decode('utf-8', 'replace')
    return ''


def _parse_nav(raw_bytes):
    msg = email.message_from_bytes(raw_bytes)
    subject = _decode_subject(msg.get('Subject', ''))
    if ('SXR047' not in subject) and ('琰知' not in subject) and ('基金净值' not in subject):
        return None
    body = _extract_body(msg)
    if not body:
        return None
    m = re.search(r'SXR047[^|\n]*\|[^|\n]*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)', body)
    if not m:
        m = re.search(r'SXR047[^\n]*?(\d{4}-\d{2}-\d{2})\s+([\d.]+)', body)
    if not m:
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', body)
        nav_match = re.search(r'(?:净值|NAV|nav|单位净值)[^\d]{0,10}((?:0\.[5-9]|[1-4]\.)\d{3,4})\b', body)
        if date_match and nav_match:
            class _M: pass
            m = _M()
            m.group = lambda i: [None, date_match.group(1), nav_match.group(1)][i]
    if m:
        return {'date': m.group(1), 'nav': float(m.group(2)), 'subject': subject}
    return None


def _atomic_write(path, content):
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _update_html(date, nav):
    content = HTML_PATH.read_text(encoding='utf-8')
    if re.search(rf'date:\s*["\']{re.escape(date)}["\']', content):
        return False
    entry = f'    {{ date: "{date}", nav: {nav} }}'
    for name in ('personalNav', 'fundHistory'):
        pattern = re.compile(rf'({re.escape(name)}\s*:\s*\[)([\s\S]*?)(\s*\])')
        mm = pattern.search(content)
        if not mm:
            raise ValueError(f'未在 HTML 中找到 {name} 数组')
        body = mm.group(2)
        last_brace = re.search(r'\}(?=[ \t]*(//[^\n]*)?\s*$)', body, re.MULTILINE)
        if last_brace:
            tail = body[last_brace.end():]
            skipped = re.match(r'[ \t]*(?://[^\n]*)?', tail)
            after = tail[skipped.end() if skipped else 0:].lstrip()
            if not after.startswith(','):
                insert_pos = last_brace.end()
                body = body[:insert_pos] + ',' + body[insert_pos:]
        body = body.rstrip() + '\n' + entry + '\n  '
        content = content[:mm.start()] + mm.group(1) + '\n' + body + mm.group(3) + content[mm.end():]
    content = re.sub(r'latestDate\s*:\s*["\'][\d-]+["\']',
                     f'latestDate: "{date}"', content)
    _atomic_write(HTML_PATH, content)
    return True


def _run_git(*cmd):
    r = subprocess.run(cmd, cwd=REPO_PATH, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or '').strip()
        raise RuntimeError(f'命令失败: {" ".join(cmd)}\n{msg}')
    return r.stdout.strip()


def _do_refresh():
    if not IMAP_USER or not IMAP_PASSWORD:
        return {'ok': False, 'error': 'IMAP_USER 或 IMAP_PASSWORD 未配置'}

    try:
        from imapclient import IMAPClient
    except ImportError:
        return {'ok': False, 'error': 'imapclient 未安装'}

    import ssl
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_ctx = ssl.create_default_context()

    client = None
    try:
        use_proxy = PROXY_HOST and PROXY_PORT
        if use_proxy:
            try:
                import socks
                proxy_user = PROXY_USERNAME or None
                proxy_pass = PROXY_PASSWORD or None
                s = socks.socksocket()
                s.set_proxy(socks.SOCKS5, PROXY_HOST, PROXY_PORT,
                            username=proxy_user, password=proxy_pass)
                s.connect((IMAP_SERVER, IMAP_PORT))
                tls_sock = ssl_ctx.wrap_socket(s, server_hostname=IMAP_SERVER)
                client = IMAPClient(IMAP_SERVER, port=IMAP_PORT, ssl=True,
                                    ssl_context=ssl_ctx, timeout=30, socket=tls_sock)
            except ImportError:
                client = IMAPClient(IMAP_SERVER, port=IMAP_PORT, ssl=True,
                                    ssl_context=ssl_ctx, timeout=30)
        else:
            client = IMAPClient(IMAP_SERVER, port=IMAP_PORT, ssl=True,
                                ssl_context=ssl_ctx, timeout=30)

        client.login(IMAP_USER, IMAP_PASSWORD)
        if '163.com' in IMAP_SERVER or '126.com' in IMAP_SERVER or 'yeah.net' in IMAP_SERVER:
            client.id_({'name': 'family-fund-web', 'version': '1.0', 'vendor': 'local'})
        client.select_folder('INBOX')

        since = (_dt.date.today() - _dt.timedelta(days=7)).strftime('%d-%b-%Y')
        uids = client.search(['SINCE', since])
        nav_keywords = ['SXR047', '琰知', '基金净值']
        nav_uids = []
        if uids:
            fetched = client.fetch(uids, ['ENVELOPE'])
            for uid in uids:
                if uid not in fetched:
                    continue
                env = fetched[uid][b'ENVELOPE']
                subj_raw = env.subject
                subj = _decode_subject(subj_raw)
                if any(kw in subj for kw in nav_keywords):
                    nav_uids.append(uid)

        if not nav_uids:
            return {'ok': True, 'updated': False, 'message': '近7天没有净值邮件'}

        fetched_all = []
        for uid in nav_uids:
            try:
                data = client.fetch([uid], ['RFC822'])
                if uid in data:
                    raw = data[uid][b'RFC822']
                    info = _parse_nav(raw)
                    if info:
                        fetched_all.append(info)
            except Exception:
                continue

        if not fetched_all:
            return {'ok': True, 'updated': False, 'message': '找到邮件但未解析到净值数据'}

        fetched_all.sort(key=lambda x: x['date'], reverse=True)
        latest = fetched_all[0]
        updated = _update_html(latest['date'], latest['nav'])

        if updated:
            _run_git('git', 'config', 'user.name', 'family-nav-monitor')
            _run_git('git', 'config', 'user.email', 'monitor@localhost')
            _run_git('git', 'add', 'index.html')
            _run_git('git', 'commit', '-m', f'净值手动更新 {latest["date"]} NAV={latest["nav"]}')
            github_token = os.environ.get('GITHUB_TOKEN', '')
            github_user = os.environ.get('GITHUB_USER', 'redclawcecilia-tech')
            github_repo = os.environ.get('GITHUB_REPO', 'family')
            if github_token:
                r = subprocess.run(
                    ['git', 'push',
                     f'https://{github_user}:{github_token}@github.com/{github_user}/{github_repo}.git',
                     'HEAD:main'],
                    cwd=REPO_PATH, capture_output=True, text=True, check=False)
                if r.returncode != 0:
                    msg = (r.stderr or r.stdout or '').replace(github_token, '***').strip()
                    raise RuntimeError(f'git push 失败: {msg}')
            else:
                _run_git('git', 'push', 'origin', 'main')
            return {'ok': True, 'updated': True,
                    'message': f'已更新: {latest["date"]} 净值 {latest["nav"]}'}
        else:
            return {'ok': True, 'updated': False,
                    'message': f'最新净值 {latest["date"]} = {latest["nav"]} 已是最新，无需更新'}

    except Exception as e:
        log.exception('刷新失败')
        return {'ok': False, 'error': str(e)}
    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'FamilyFund/1.0'

    def log_message(self, fmt, *args):
        log.info(f'{self.client_address[0]} - {fmt % args}')

    def do_GET(self):
        try:
            path = urlparse(unquote(self.path)).path
            if path == '/api/refresh':
                self._handle_refresh()
                return
            mapped = ALLOWED.get(path)
            if not mapped:
                self._404()
                return
            file = REPO_PATH / mapped
            if not file.exists() or not file.is_file():
                self._404()
                return
            data = file.read_bytes()
            ext = file.suffix.lower()
            ctype = MIME.get(ext, 'application/octet-stream')

            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'SAMEORIGIN')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            pass
        except Exception:
            log.exception('处理请求出错')
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _handle_refresh(self):
        log.info('📥 收到手动刷新请求')

        # 1) 拿到结果——挂线程跑 _do_refresh，墙钟超时 15s。
        #    （IMAPClient 的 timeout 只覆盖单个 socket 读，多步操作累加可能 >60s）
        import threading
        _holder = {'result': None}

        def _runner():
            try:
                r = _do_refresh()
                if not isinstance(r, dict):
                    r = {'ok': False,
                         'error': f'内部错误：_do_refresh 返回非 dict ({type(r).__name__})'}
                _holder['result'] = r
            except BaseException as e:
                log.exception('_do_refresh 抛出未捕获异常')
                _holder['result'] = {'ok': False,
                                     'error': f'刷新失败 [{type(e).__name__}] {e}'}

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=15)

        if t.is_alive():
            log.warning('_do_refresh 在 15 秒内未返回（IMAP 卡住）')
            result = {'ok': False,
                      'error': '刷新超时（>15s）—— IMAP 服务器无响应。可能 imap.163.com 出站不通、163 授权码失效、或网络抖动。请到服务器看 logs/web.log。'}
        else:
            result = _holder['result']
            if result is None:
                result = {'ok': False, 'error': '内部错误：_do_refresh 返回 None'}

        # 2) 序列化（理论上不会失败，但仍然兜底）
        try:
            body = json.dumps(result, ensure_ascii=False).encode('utf-8')
        except Exception as e:
            log.exception('JSON 序列化失败')
            body = (
                '{"ok": false, "error": "响应序列化失败: '
                + str(e).replace('"', "'")
                + '"}'
            ).encode('utf-8', 'replace')

        # 3) 写响应（客户端可能已断开，必须捕获 socket 异常）
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Refresh-Version', '2')  # bump on each /api/refresh patch
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log.warning('客户端在响应前断开')
        except Exception:
            log.exception('写响应失败')

    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def _404(self):
        self.send_response(404)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write('Not Found'.encode())


def main():
    log.info('=' * 50)
    log.info(f'📡 启动 Web 服务')
    log.info(f'📁 根目录: {REPO_PATH}')
    log.info(f'🌐 监听:   http://{HOST}:{PORT}/')
    log.info(f'🔄 刷新:   http://{HOST}:{PORT}/api/refresh')
    log.info('=' * 50)

    if not (REPO_PATH / 'index.html').exists():
        log.error('❌ 未找到 index.html，请检查 REPO_PATH')
        return

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info('手动停止')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
