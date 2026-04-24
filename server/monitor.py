#!/usr/bin/env python3
"""
青琰琰知一号 净值事件驱动监听
- IMAP IDLE 保持与 Gmail 的长连接（非轮询）
- 【基金净值】邮件一到 Gmail，服务器立刻收到推送
- 解析净值 → 更新 index.html → git commit + push
- GitHub 触发 Cloudflare Pages 自动重新部署

配置：通过环境变量（见 config.env.example）
"""
import os
import re
import sys
import time
import email
import logging
import subprocess
from pathlib import Path
from email.header import decode_header

try:
    from imapclient import IMAPClient
    from imapclient.exceptions import IMAPClientError
except ImportError:
    print('缺少 imapclient 库。请执行：pip install imapclient')
    sys.exit(1)

# ============ 自动加载 config.env（同目录下）============
_cfg = Path(__file__).resolve().parent / 'config.env'
if _cfg.exists():
    for _ln in _cfg.read_text(encoding='utf-8').splitlines():
        _ln = _ln.strip()
        if not _ln or _ln.startswith('#') or '=' not in _ln:
            continue
        _k, _v = _ln.split('=', 1)
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        os.environ.setdefault(_k, _v)

# ============ 配置 ============
GMAIL_USER = os.environ['GMAIL_USER']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD'].replace(' ', '')  # 去掉空格
REPO_PATH = Path(os.environ.get('REPO_PATH', Path(__file__).resolve().parent.parent))
GITHUB_USER = os.environ.get('GITHUB_USER', 'redclawcecilia-tech')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'family')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
HTML_PATH = REPO_PATH / 'index.html'

# ============ 日志 ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('nav-monitor')


# ============ 邮件解析 ============
def decode_subject(raw_subject):
    if not raw_subject:
        return ''
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
    """从 email.message 提取 text/plain 正文"""
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
    """从原始邮件字节解析 { date, nav }；匹配不到返回 None"""
    msg = email.message_from_bytes(raw_bytes)
    subject = decode_subject(msg.get('Subject', ''))

    if ('SXR047' not in subject) and ('琰知' not in subject) and ('基金净值' not in subject):
        return None

    body = extract_body(msg)
    # 典型正文：| SXR047(A级) | 青琰琰知一号... | 2026-04-22 | 1.2966 | 1.3456 | ...
    m = re.search(
        r'SXR047[^|\n]*\|[^|\n]*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)',
        body,
        re.MULTILINE
    )
    if m:
        return {
            'date': m.group(1),
            'nav': float(m.group(2)),
            'subject': subject,
        }
    return None


# ============ HTML 更新 ============
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
        body = mm.group(2).rstrip()
        if body and not body.endswith(','):
            body += ','
        body += '\n' + entry + '\n  '
        content = content[:mm.start()] + mm.group(1) + '\n' + body + mm.group(3) + content[mm.end():]

    content = re.sub(r'latestDate\s*:\s*["\'][\d-]+["\']',
                     f'latestDate: "{date}"', content)

    HTML_PATH.write_text(content, encoding='utf-8')
    return True


# ============ Git 推送 ============
def git_push(date, nav):
    def run(*cmd):
        log.info('$ ' + ' '.join(cmd))
        r = subprocess.run(cmd, cwd=REPO_PATH, capture_output=True, text=True)
        if r.returncode != 0:
            log.warning(r.stderr.strip())
        return r.returncode == 0

    # 确保有 git identity
    subprocess.run(['git', 'config', 'user.name', 'family-nav-monitor'],
                   cwd=REPO_PATH, check=False)
    subprocess.run(['git', 'config', 'user.email', 'monitor@localhost'],
                   cwd=REPO_PATH, check=False)

    run('git', 'add', 'index.html')
    run('git', 'commit', '-m', f'净值自动更新 {date} NAV={nav}')

    if GITHUB_TOKEN:
        remote_url = f'https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git'
        subprocess.run(['git', 'push', remote_url, 'HEAD:main'],
                       cwd=REPO_PATH, check=False)
    else:
        run('git', 'push', 'origin', 'main')


# ============ 处理一封邮件 ============
def process_uid(client, uid):
    try:
        fetched = client.fetch([uid], ['RFC822'])
        if uid not in fetched:
            return
        raw = fetched[uid][b'RFC822']
        info = parse_nav(raw)
        if not info:
            return
        log.info(f'🎯 解析到净值 {info["date"]} = {info["nav"]} （主题: {info["subject"][:60]}）')
        if update_html(info['date'], info['nav']):
            git_push(info['date'], info['nav'])
            log.info('✅ 已推送到 GitHub，Cloudflare 约 30-60 秒后部署完成')
    except Exception:
        log.exception(f'处理 uid={uid} 失败')


# ============ 主循环 ============
def main():
    log.info('=' * 60)
    log.info(f'📬 启动 Gmail 监听 · 账号: {GMAIL_USER}')
    log.info(f'📁 仓库路径: {REPO_PATH}')
    log.info(f'🔗 GitHub: {GITHUB_USER}/{GITHUB_REPO}')
    log.info('=' * 60)

    while True:
        try:
            with IMAPClient('imap.gmail.com', port=993, ssl=True) as client:
                client.login(GMAIL_USER, GMAIL_APP_PASSWORD)
                client.select_folder('INBOX')
                log.info('🔐 已登录 Gmail')

                # 处理启动时的未读【基金净值】邮件
                missed = client.search(['UNSEEN', 'SUBJECT', 'SXR047'])
                if missed:
                    log.info(f'📥 发现 {len(missed)} 封未读净值邮件，逐一处理')
                    for uid in missed:
                        process_uid(client, uid)

                # 进入 IDLE 模式 —— 事件驱动，非轮询
                while True:
                    client.idle()
                    log.info('👂 IDLE 等待新邮件...')
                    # IMAP IDLE 最多维持 29 分钟，Google 建议 ≤ 30 分钟，取 25 分钟保守
                    responses = client.idle_check(timeout=25 * 60)
                    client.idle_done()

                    if responses:
                        log.info(f'🔔 收到 IMAP 通知: {len(responses)} 个事件')
                        # 查找新的未读净值邮件
                        new_uids = client.search(['UNSEEN', 'SUBJECT', 'SXR047'])
                        for uid in new_uids:
                            process_uid(client, uid)
                    else:
                        log.info('⏱ IDLE 超时，重连...')
                        break  # 跳出 IDLE 循环，重新登录（避免僵尸连接）

        except IMAPClientError as e:
            log.warning(f'IMAP 错误: {e}，30 秒后重连')
            time.sleep(30)
        except KeyboardInterrupt:
            log.info('手动停止')
            break
        except Exception:
            log.exception('意外错误，60 秒后重连')
            time.sleep(60)


if __name__ == '__main__':
    main()
