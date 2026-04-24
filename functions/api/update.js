// Cloudflare Pages Function: /api/update
// 接收前端提交的净值 → 更新 index.html 中的数组 → commit 到 GitHub → CF 自动重新部署

const OWNER = 'redclawcecilia-tech';
const REPO = 'family';
const PATH = 'index.html';
const BRANCH = 'main';

export async function onRequestPost(context) {
  const { request, env } = context;

  // 检查 Token 是否配置
  if (!env.GITHUB_TOKEN) {
    return json({ error: 'Server missing GITHUB_TOKEN env var' }, 500);
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return json({ error: 'Invalid JSON body' }, 400);
  }

  const { date, nav } = payload;
  if (!date || !nav || isNaN(Number(nav))) {
    return json({ error: '请提供有效的 date 和 nav', received: payload }, 400);
  }

  // 简单日期格式校验
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return json({ error: '日期格式应为 YYYY-MM-DD' }, 400);
  }
  const navNum = Number(nav);
  if (navNum < 0.5 || navNum > 5) {
    return json({ error: '净值应在合理区间（0.5-5）' }, 400);
  }

  try {
    // 1. 从 GitHub 读取当前 index.html
    const getResp = await fetch(
      `https://api.github.com/repos/${OWNER}/${REPO}/contents/${PATH}?ref=${BRANCH}`,
      {
        headers: {
          Authorization: `token ${env.GITHUB_TOKEN}`,
          'User-Agent': 'family-fund-update',
          Accept: 'application/vnd.github.v3+json',
        },
      }
    );
    if (!getResp.ok) {
      const txt = await getResp.text();
      return json({ error: 'GitHub read failed', status: getResp.status, detail: txt.slice(0, 300) }, 502);
    }
    const fileData = await getResp.json();
    const currentSha = fileData.sha;
    const currentContent = b64decode(fileData.content);

    // 2. 检查是否已经有这个日期（避免重复追加）
    const dupRegex = new RegExp(`date:\\s*["']${escapeRegex(date)}["']`);
    if (dupRegex.test(currentContent)) {
      return json({ error: `日期 ${date} 的净值已存在，如需覆盖请先在 GitHub 上编辑或联系 Claude`, duplicate: true }, 409);
    }

    // 3. 插入新记录到 personalNav 和 fundHistory 两个数组末尾
    const newEntry = `    { date: "${date}", nav: ${navNum} }`;
    let updated = currentContent;

    updated = insertIntoArray(updated, 'personalNav', newEntry);
    updated = insertIntoArray(updated, 'fundHistory', newEntry);

    if (updated === currentContent) {
      return json({ error: '未能在 HTML 中找到 personalNav 或 fundHistory 数组，请检查模板' }, 500);
    }

    // 4. 同时更新 latestDate 字段（可选优化）
    updated = updated.replace(
      /latestDate:\s*["'][\d-]+["']/,
      `latestDate: "${date}"`
    );

    // 5. commit 回 GitHub
    const encodedNew = b64encode(updated);
    const commitMessage = `净值更新 ${date} NAV=${navNum}（来自网页按钮）`;

    const putResp = await fetch(
      `https://api.github.com/repos/${OWNER}/${REPO}/contents/${PATH}`,
      {
        method: 'PUT',
        headers: {
          Authorization: `token ${env.GITHUB_TOKEN}`,
          'User-Agent': 'family-fund-update',
          'Content-Type': 'application/json',
          Accept: 'application/vnd.github.v3+json',
        },
        body: JSON.stringify({
          message: commitMessage,
          content: encodedNew,
          sha: currentSha,
          branch: BRANCH,
        }),
      }
    );

    if (!putResp.ok) {
      const txt = await putResp.text();
      return json({ error: 'GitHub commit failed', status: putResp.status, detail: txt.slice(0, 300) }, 502);
    }

    const commitInfo = await putResp.json();
    return json({
      success: true,
      date,
      nav: navNum,
      commitSha: commitInfo.commit?.sha?.slice(0, 7),
      message: '已提交到 GitHub，Cloudflare 将在 30–60 秒内重新部署',
      refreshInSeconds: 45,
    });
  } catch (err) {
    return json({ error: 'Exception', detail: String(err).slice(0, 300) }, 500);
  }
}

// 允许 OPTIONS 预检（如果跨域）
export async function onRequestOptions() {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
    },
  });
}

function b64decode(s) {
  // GitHub 返回的 base64 含换行
  const clean = s.replace(/\n/g, '');
  // 使用 atob 解码为 UTF-8
  const bin = atob(clean);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder('utf-8').decode(bytes);
}

function b64encode(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// 在数组末尾插入一行（智能查找 ] 闭合位置）
function insertIntoArray(src, arrayName, newEntryLine) {
  const re = new RegExp(`(${arrayName}:\\s*\\[)([\\s\\S]*?)(\\s*\\])`);
  const m = src.match(re);
  if (!m) return src;
  const header = m[1];
  const body = m[2];
  const closing = m[3];

  // 如果 body 不是以逗号结尾，加逗号
  let newBody = body.trimEnd();
  if (!newBody.endsWith(',') && newBody.length > 0) {
    newBody += ',';
  }
  newBody += '\n' + newEntryLine + '\n  ';

  return src.replace(re, header + '\n' + newBody + closing);
}
