#!/usr/bin/env python3
"""
自动更新父母基金报告 HTML 中的净值
- 用 Gmail OAuth refresh_token 换取 access_token
- 搜索最新【基金净值】邮件并解析单位净值
- 更新 index.html 的 personalNav / fundHistory 数组
- 如无变化则什么都不做

环境变量：
  GMAIL_CLIENT_ID
  GMAIL_CLIENT_SECRET
  GMAIL_REFRESH_TOKEN
  GITHUB_OUTPUT (由 GitHub Actions 自动设置，用来传值给下一步)
"""
import os
import re
import sys
import json
import base64
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError

HTML_PATH = Path(__file__).resolve().parent.parent / 'index.html'


def get_access_token():
    """用 refresh_token 换取短期 access_token"""
    client_id = os.environ['GMAIL_CLIENT_ID']
    client_secret = os.environ['GMAIL_CLIENT_SECRET']
    refresh_token = os.environ['GMAIL_REFRESH_TOKEN']

    data = urlencode({
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }).encode()
    req = Request('https://oauth2.googleapis.com/token', data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read())
    except HTTPError as e:
        err_body = e.read().decode('utf-8', 'replace')
        raise SystemExit(f'❌ OAuth token exchange failed ({e.code}): {err_body}')
    return body['access_token']


def gmail_get(path, access_token):
    """调用 Gmail REST API"""
    req = Request(f'https://gmail.googleapis.com/gmail/v1/users/me{path}')
    req.add_header('Authorization', f'Bearer {access_token}')
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def decode_body_data(data_b64):
    """Gmail 用 base64url 编码邮件正文"""
    # 补齐 =
    padding = 4 - len(data_b64) % 4
    if padding != 4:
        data_b64 += '=' * padding
    raw = base64.urlsafe_b64decode(data_b64)
    return raw.decode('utf-8', 'replace')


def extract_plain_text(payload):
    """递归找 text/plain 部分"""
    if payload.get('mimeType', '').startswith('text/plain'):
        data = payload.get('body', {}).get('data', '')
        if data:
            return decode_body_data(data)
    for part in payload.get('parts', []) or []:
        txt = extract_plain_text(part)
        if txt:
            return txt
    return ''


def find_latest_nav_email(access_token):
    """
    搜索最新【基金净值】邮件，返回 { date: YYYY-MM-DD, nav: float } 或 None
    """
    q = 'subject:(SXR047 OR "琰知一号") newer_than:14d'
    result = gmail_get(f'/messages?q={urlencode({"q": q})[2:]}&maxResults=20', access_token)
    msgs = result.get('messages', [])
    if not msgs:
        return None

    # 可能有多封，优先选日期最新且含 NAV 数据的
    best = None
    for m in msgs:
        detail = gmail_get(f'/messages/{m["id"]}?format=full', access_token)
        body = extract_plain_text(detail.get('payload', {}))
        # 匹配类似：| SXR047(A级) | ... | 2026-04-22 | 1.2966 | ...
        pattern = re.compile(
            r'SXR047[^|\n]*\|[^|\n]*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)',
            re.MULTILINE
        )
        match = pattern.search(body)
        if match:
            date_str = match.group(1)
            nav = float(match.group(2))
            if best is None or date_str > best['date']:
                best = {'date': date_str, 'nav': nav, 'message_id': m['id']}
    return best


def insert_into_array(src, array_name, entry_line):
    """在 JS 数组末尾追加一行"""
    pattern = re.compile(
        rf'({re.escape(array_name)}\s*:\s*\[)([\s\S]*?)(\s*\])',
        re.MULTILINE
    )
    m = pattern.search(src)
    if not m:
        raise ValueError(f'未在 HTML 中找到 {array_name} 数组')
    header, body, closing = m.group(1), m.group(2), m.group(3)
    body_rstripped = body.rstrip()
    # 如果不是空数组且最后一行不是逗号，补逗号
    if body_rstripped and not body_rstripped.endswith(','):
        body_rstripped += ','
    new_body = body_rstripped + '\n' + entry_line + '\n  '
    return src[:m.start()] + header + '\n' + new_body + closing + src[m.end():]


def update_html(date, nav):
    """修改 index.html，返回 True 表示实际修改"""
    content = HTML_PATH.read_text(encoding='utf-8')

    # 已存在则跳过
    if re.search(rf'date:\s*["\']{re.escape(date)}["\']', content):
        print(f'⏭️  日期 {date} 的净值已存在，跳过')
        return False

    entry = f'    {{ date: "{date}", nav: {nav} }}'
    content = insert_into_array(content, 'personalNav', entry)
    content = insert_into_array(content, 'fundHistory', entry)

    # 更新 latestDate（若有）
    content = re.sub(r'latestDate\s*:\s*["\'][\d-]+["\']',
                     f'latestDate: "{date}"', content)

    HTML_PATH.write_text(content, encoding='utf-8')
    return True


def write_output(key, value):
    """写入 GitHub Actions 的 outputs"""
    out_path = os.environ.get('GITHUB_OUTPUT')
    if out_path:
        with open(out_path, 'a', encoding='utf-8') as f:
            f.write(f'{key}={value}\n')
    print(f'[output] {key}={value}')


def main():
    print('🔑 换取 access_token...')
    token = get_access_token()
    print('📬 搜索最新【基金净值】邮件...')
    info = find_latest_nav_email(token)
    if not info:
        print('📭 未找到近 14 天的【基金净值】邮件')
        write_output('changed', 'false')
        return
    print(f'✅ 找到净值：{info["date"]} → {info["nav"]}')
    changed = update_html(info['date'], info['nav'])
    write_output('changed', 'true' if changed else 'false')
    write_output('date', info['date'])
    write_output('nav', str(info['nav']))


if __name__ == '__main__':
    main()
