#!/usr/bin/env python3
"""
极简 HTTP Server —— 对外 serve 父母基金报告 index.html
- 只 serve 仓库根目录下的 index.html（以及 favicon 等静态文件）
- 禁止目录浏览、禁止访问 server/、.git/ 等敏感目录
- 设置合理的缓存头（总是刷新，避免父母看到旧数据）
"""
import os
import logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

# 自动加载 config.env（与 monitor.py 相同目录）
_cfg = Path(__file__).resolve().parent / 'config.env'
if _cfg.exists():
    for _ln in _cfg.read_text(encoding='utf-8').splitlines():
        _ln = _ln.strip()
        if not _ln or _ln.startswith('#') or '=' not in _ln:
            continue
        _k, _v = _ln.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

HOST = os.environ.get('WEB_HOST', '0.0.0.0')
PORT = int(os.environ.get('WEB_PORT', '8080'))
REPO_PATH = Path(os.environ.get('REPO_PATH', Path(__file__).resolve().parent.parent))

# 允许访问的文件（白名单）
ALLOWED = {
    '/': 'index.html',
    '/index.html': 'index.html',
    '/favicon.ico': 'favicon.ico',   # 如果没有就 404
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


class Handler(BaseHTTPRequestHandler):
    server_version = 'FamilyFund/1.0'

    def log_message(self, fmt, *args):
        log.info(f'{self.client_address[0]} - {fmt % args}')

    def do_GET(self):
        try:
            path = urlparse(unquote(self.path)).path
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
            # 每次都读最新文件（monitor 可能正在更新），禁止客户端缓存
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            # 安全头
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

    def do_HEAD(self):
        self.do_GET()

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
