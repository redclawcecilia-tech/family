#!/usr/bin/env python3
"""family 报告页发布保护：渲染自检、原子写入、失败回滚。"""
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger('html-guard')
RENDER_CHECK_PATH = Path(__file__).resolve().with_name('render_check.js')
RENDER_CHECK_TIMEOUT = int(os.environ.get('RENDER_CHECK_TIMEOUT', '20') or '20')


class RenderCheckError(RuntimeError):
    """页面未通过渲染自检。"""


def _find_node():
    candidates = [
        os.environ.get('NODE_BINARY', ''),
        shutil.which('node') or '',
        r'C:\Program Files\nodejs\node.exe',
        '/usr/bin/node',
        '/usr/local/bin/node',
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RenderCheckError('未找到 Node.js，已停止发布未经渲染自检的页面')


def validate_html(path):
    """执行页面脚本并检查关键内容；失败时抛出 RenderCheckError。"""
    path = Path(path)
    if not RENDER_CHECK_PATH.is_file():
        raise RenderCheckError(f'缺少渲染自检脚本: {RENDER_CHECK_PATH}')
    try:
        result = subprocess.run(
            [_find_node(), str(RENDER_CHECK_PATH), str(path)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=RENDER_CHECK_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderCheckError(f'渲染自检超时（>{RENDER_CHECK_TIMEOUT}s）') from exc
    output = '\n'.join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode != 0 or 'RENDER CHECK OK' not in result.stdout:
        raise RenderCheckError(output or f'渲染自检进程异常退出（{result.returncode}）')
    return output


def _atomic_write_bytes(path, data):
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_html_checked(path, content):
    """候选页面先自检再发布；发布后复检失败则恢复原始字节。"""
    path = Path(path)
    original = path.read_bytes() if path.exists() else None
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.candidate.html')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as file:
            file.write(content)
        validate_html(temp_name)
        os.replace(temp_name, path)
        temp_name = None
        try:
            output = validate_html(path)
        except Exception:
            log.exception('页面发布后复检失败，正在恢复上一版')
            if original is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write_bytes(path, original)
            raise
        log.info('页面渲染自检通过: %s', path)
        return output
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
