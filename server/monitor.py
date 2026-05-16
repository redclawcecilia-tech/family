#!/usr/bin/env python3
"""
青琰琰知一号 净值事件驱动监听
- IMAP IDLE 保持与邮箱的长连接（非轮询）
- 网易邮箱不支持 IDLE 时自动回退到轮询模式
- 【基金净值】邮件一到邮箱，服务器立刻收到推送
- 解析净值 → 更新 index.html → git commit + push
- GitHub 触发 Cloudflare Pages 自动重新部署

配置：通过环境变量或 config.env（见 config.env.example）
"""
import os
import re
import sys
import time
import json
import email
import socket
import logging
import tempfile
import subprocess
from pathlib import Path
from email.header import decode_header

try:
    from imapclient import IMAPClient
    from imapclient.exceptions import IMAPClientError
except ImportError:
    print('缺少 imapclient 库。请执行：pip install imapclient')
    sys.exit(1)

import ssl
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None

IMAP_MAX_RETRIES = 3
IMAP_RETRY_DELAY = 10
IMAP_CONNECT_TIMEOUT = 30
IDLE_TIMEOUT = 25 * 60

# ============ 自动加载 config.env（同目录下）============
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

# ============ 配置 ============
IMAP_SERVER = os.environ.get('IMAP_SERVER', 'imap.163.com')
IMAP_PORT = int(os.environ.get('IMAP_PORT', '993') or '993')
IMAP_USER = os.environ['IMAP_USER']
IMAP_PASSWORD = os.environ['IMAP_PASSWORD'].replace(' ', '')
REPO_PATH = Path(os.environ.get('REPO_PATH', Path(__file__).resolve().parent.parent))
GITHUB_USER = os.environ.get('GITHUB_USER', 'redclawcecilia-tech')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'family')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
HTML_PATH = REPO_PATH / 'index.html'

PROXY_HOST = os.environ.get('IMAP_PROXY_HOST', '') or ''
PROXY_PORT = int(os.environ.get('IMAP_PROXY_PORT', '0') or '0')
PROXY_USERNAME = os.environ.get('IMAP_PROXY_USERNAME', '') or ''
PROXY_PASSWORD = os.environ.get('IMAP_PROXY_PASSWORD', '') or ''

POLL_INTERVAL = int(os.environ.get('POLL_INTERVAL', '60') or '60')

PROCESSED_UIDS_FILE = Path(__file__).resolve().parent / 'processed_uids.json'

# ============ 日志 ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('nav-monitor')


# ============ 已处理 UID 追踪 ============
def _load_processed_uids():
    if PROCESSED_UIDS_FILE.exists():
        try:
            return {str(uid) for uid in json.loads(PROCESSED_UIDS_FILE.read_text(encoding='utf-8'))}
        except Exception:
            return set()
    return set()


def _save_processed_uids(uids):
    try:
        PROCESSED_UIDS_FILE.write_text(
            json.dumps(sorted(str(u) for u in uids)), encoding='utf-8')
    except Exception:
        pass


# ============ 邮件解析 ============
def decode_subject(raw_subject):
    if not raw_subject:
        return ''
    if isinstance(raw_subject, bytes):
        try:
            raw_subject = raw_subject.decode('utf-8', errors='replace')
        except Exception:
            raw_subject = str(raw_subject)
    parts = decode_header(raw_subject)
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


def extract_body(msg):
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


def parse_nav(raw_bytes):
    msg = email.message_from_bytes(raw_bytes)
    subject = decode_subject(msg.get('Subject', ''))

    if ('SXR047' not in subject) and ('琰知' not in subject) and ('基金净值' not in subject):
        return None

    body = extract_body(msg)
    if not body:
        return None

    m = re.search(
        r'SXR047[^|\n]*\|[^|\n]*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)',
        body
    )
    if not m:
        m = re.search(
            r'SXR047[^\n]*?(\d{4}-\d{2}-\d{2})\s+([\d.]+)',
            body
        )
    if not m:
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', body)
        nav_match = re.search(
            r'(?:净值|NAV|nav|单位净值)[^\d]{0,10}((?:0\.[5-9]|[1-4]\.)\d{3,4})\b',
            body
        )
        if date_match and nav_match:
            class _M: pass
            m = _M()
            m.group = lambda i: [None, date_match.group(1), nav_match.group(1)][i]

    if m:
        return {
            'date': m.group(1),
            'nav': float(m.group(2)),
            'subject': subject,
        }

    log.warning(f'📧 "{subject[:50]}" 主题匹配但正文解析失败，正文前 300 字：')
    log.warning(body[:300].replace('\n', ' ⏎ '))
    return None


# ============ HTML 更新（原子写入）============
def update_html(date, nav):
    content = HTML_PATH.read_text(encoding='utf-8')

    if re.search(rf'date:\s*["\']{re.escape(date)}["\']', content):
        log.info(f'⏭ 日期 {date} 已存在，跳过')
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


# ============ Git 推送 ============
def git_push(date, nav):
    def run(*cmd):
        log.info('$ ' + ' '.join(cmd))
        r = subprocess.run(cmd, cwd=REPO_PATH, capture_output=True, text=True)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or '').strip()
            log.error(msg)
            raise RuntimeError(f'命令失败: {" ".join(cmd)}\n{msg}')
        return r.stdout.strip()

    subprocess.run(['git', 'config', 'user.name', 'family-nav-monitor'],
                   cwd=REPO_PATH, check=False)
    subprocess.run(['git', 'config', 'user.email', 'monitor@localhost'],
                   cwd=REPO_PATH, check=False)

    run('git', 'add', 'index.html')
    run('git', 'commit', '-m', f'净值自动更新 {date} NAV={nav}')

    if GITHUB_TOKEN:
        r = subprocess.run(
            ['git', 'push',
             f'https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git',
             'HEAD:main'],
            cwd=REPO_PATH, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or '').replace(GITHUB_TOKEN, '***').strip()
            log.error(msg)
            raise RuntimeError(f'git push 失败: {msg}')
    else:
        run('git', 'push', 'origin', 'main')


# ============ 处理一封邮件 ============
def process_uid(client, uid, processed_uids):
    uid_key = str(uid)
    if uid_key in processed_uids:
        return
    try:
        fetched = client.fetch([uid], ['RFC822'])
        if uid not in fetched:
            return
        raw = fetched[uid][b'RFC822']
        info = parse_nav(raw)
        if not info:
            processed_uids.add(uid_key)
            _save_processed_uids(processed_uids)
            return
        log.info(f'🎯 解析到净值 {info["date"]} = {info["nav"]} （主题: {info["subject"][:60]}）')
        if update_html(info['date'], info['nav']):
            git_push(info['date'], info['nav'])
            log.info('✅ 已推送到 GitHub，Cloudflare 约 30-60 秒后部署完成')
        processed_uids.add(uid_key)
        _save_processed_uids(processed_uids)
    except Exception:
        log.exception(f'处理 uid={uid} 失败')


# ============ IMAP 搜索辅助 ============
def _imap_since_date(days):
    import datetime as _dt
    d = _dt.date.today() - _dt.timedelta(days=days)
    return d.strftime('%d-%b-%Y')


def _search_nav_emails(client, days):
    since = _imap_since_date(days)
    nav_keywords = ['SXR047', '琰知', '基金净值']
    uids = client.search(['SINCE', since])
    if not uids:
        return []
    try:
        fetched = client.fetch(uids, ['ENVELOPE'])
    except Exception as e:
        log.warning(f'ENVELOPE fetch 失败: {e}')
        return []
    nav_uids = []
    for uid in uids:
        if uid not in fetched:
            continue
        env = fetched[uid][b'ENVELOPE']
        subj_raw = env.subject
        subj = decode_subject(subj_raw)
        if any(kw in subj for kw in nav_keywords):
            nav_uids.append(uid)
    return nav_uids


# ============ 连接 ============
def _make_proxy_sock(host, port):
    try:
        import socks
        proxy_user = PROXY_USERNAME or None
        proxy_pass = PROXY_PASSWORD or None
        s = socks.socksocket()
        s.set_proxy(socks.SOCKS5, host, port,
                    username=proxy_user, password=proxy_pass)
        return s
    except ImportError:
        log.warning('PySocks未安装，无法使用代理。请运行: pip install PySocks')
        return None


def _connect_imap():
    use_proxy = PROXY_HOST and PROXY_PORT
    if use_proxy:
        log.info(f'🌐 使用SOCKS5代理: {PROXY_HOST}:{PROXY_PORT}')

    for attempt in range(1, IMAP_MAX_RETRIES + 1):
        try:
            log.info(f'📬 连接 {IMAP_SERVER}:{IMAP_PORT} (尝试 {attempt}/{IMAP_MAX_RETRIES})')

            if use_proxy:
                sock = _make_proxy_sock(PROXY_HOST, PROXY_PORT)
                if sock:
                    sock.connect((IMAP_SERVER, IMAP_PORT))
                    import ssl as _ssl
                    ssl_ctx = _SSL_CTX or _ssl.create_default_context()
                    tls_sock = ssl_ctx.wrap_socket(sock, server_hostname=IMAP_SERVER)
                    client = IMAPClient(IMAP_SERVER, port=IMAP_PORT, ssl=True,
                                        ssl_context=ssl_ctx, timeout=IMAP_CONNECT_TIMEOUT,
                                        socket=tls_sock)
                else:
                    client = IMAPClient(IMAP_SERVER, port=IMAP_PORT, ssl=True,
                                        ssl_context=_SSL_CTX, timeout=IMAP_CONNECT_TIMEOUT)
            else:
                client = IMAPClient(IMAP_SERVER, port=IMAP_PORT, ssl=True,
                                    ssl_context=_SSL_CTX, timeout=IMAP_CONNECT_TIMEOUT)

            client.login(IMAP_USER, IMAP_PASSWORD)
            if '163.com' in IMAP_SERVER or '126.com' in IMAP_SERVER or 'yeah.net' in IMAP_SERVER:
                client.id_({'name': 'family-fund-monitor', 'version': '1.0', 'vendor': 'local'})
            log.info(f'🔐 已登录邮箱 {IMAP_USER}')
            return client
        except (TimeoutError, socket.timeout, OSError, ConnectionError) as e:
            log.warning(f'连接尝试 {attempt}/{IMAP_MAX_RETRIES} 失败: {e}')
            if attempt < IMAP_MAX_RETRIES:
                time.sleep(IMAP_RETRY_DELAY)
            else:
                raise
        except IMAPClientError as e:
            log.error(f'邮箱认证失败: {e}')
            raise


def _check_idle_support(client):
    caps = client.capabilities()
    if caps and b'IDLE' in caps or (isinstance(caps, (list, tuple)) and 'IDLE' in caps):
        return True
    try:
        cap_list = client.capability()
        cap_str = ' '.join(str(c) for c in cap_list) if cap_list else ''
        return 'IDLE' in cap_str.upper()
    except Exception:
        return False


# ============ 主循环 ============
def main():
    log.info('=' * 60)
    log.info(f'📬 启动净值监听 · 邮箱: {IMAP_USER}')
    log.info(f'📁 仓库路径: {REPO_PATH}')
    log.info(f'🔗 GitHub: {GITHUB_USER}/{GITHUB_REPO}')
    log.info('=' * 60)

    processed_uids = _load_processed_uids()
    log.info(f'📋 已处理邮件记录: {len(processed_uids)} 条')

    while True:
        client = None
        try:
            client = _connect_imap()
            client.select_folder('INBOX')

            missed = _search_nav_emails(client, 3)
            new_missed = [u for u in missed if str(u) not in processed_uids]
            if new_missed:
                log.info(f'📥 启动扫描近 3 天发现 {len(new_missed)} 封未处理候选邮件')
                for uid in new_missed:
                    process_uid(client, uid, processed_uids)

            use_idle = _check_idle_support(client)
            if use_idle:
                log.info('👂 服务器支持 IDLE，使用事件驱动模式')
                while True:
                    try:
                        client.idle()
                        log.info('👂 IDLE 等待新邮件...')
                        responses = client.idle_check(timeout=IDLE_TIMEOUT)
                        client.idle_done()
                    except IMAPClientError as e:
                        log.warning(f'IDLE 错误: {e}，退出IDLE循环重连')
                        break

                    if responses:
                        log.info(f'🔔 收到 IMAP 通知: {len(responses)} 个事件')
                        new_uids = _search_nav_emails(client, 1)
                        for uid in new_uids:
                            process_uid(client, uid, processed_uids)
                    else:
                        log.info('⏱ IDLE 超时，重连...')
                        break
            else:
                log.info(f'🔄 服务器不支持 IDLE，使用轮询模式（间隔 {POLL_INTERVAL} 秒）')
                while True:
                    time.sleep(POLL_INTERVAL)
                    try:
                        new_uids = _search_nav_emails(client, 1)
                        new_only = [u for u in new_uids if str(u) not in processed_uids]
                        if new_only:
                            log.info(f'🔔 轮询发现 {len(new_only)} 封新邮件')
                            for uid in new_only:
                                process_uid(client, uid, processed_uids)
                    except IMAPClientError as e:
                        log.warning(f'轮询查询失败: {e}，重连')
                        break

        except IMAPClientError as e:
            log.warning(f'IMAP 错误: {e}，30 秒后重连')
            time.sleep(30)
        except (TimeoutError, socket.timeout) as e:
            log.error(f'连接超时: {e}，60 秒后重连')
            time.sleep(60)
        except KeyboardInterrupt:
            log.info('手动停止')
            break
        except Exception:
            log.exception('意外错误，60 秒后重连')
            time.sleep(60)
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass


if __name__ == '__main__':
    main()
