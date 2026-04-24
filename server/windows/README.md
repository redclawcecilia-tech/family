# Windows 服务器部署指南

方舟云 Windows 服务器专用。如果您的服务器是 Linux，请看 `../README.md`。

## 准备工作

1. **远程桌面**登录到您的 Windows 服务器（用方舟云控制台或 RDP 客户端）
2. 准备好 Gmail 应用专用密码（16 位，从 https://myaccount.google.com/apppasswords 获取）
3. 准备好 GitHub Personal Access Token（带 `repo` 权限）

---

## 一、安装 Python + Git（10 分钟）

### 1.1 安装 Python 3.11

1. 浏览器打开 https://www.python.org/downloads/
2. 点黄色 **"Download Python 3.12.x"** 按钮下载 Windows installer
3. 双击安装包
4. **⚠️ 重要：** 安装第一个页面底部必须勾 ✅ **"Add python.exe to PATH"**
5. 点 "Install Now"
6. 装完开一个 **PowerShell** 窗口（开始菜单搜 powershell），输入：
   ```powershell
   python --version
   ```
   应该输出 `Python 3.12.x`

### 1.2 安装 Git

1. 浏览器打开 https://git-scm.com/download/win
2. 下载 64-bit Git for Windows Setup
3. 双击安装，一路点 Next 即可（默认选项都合适）
4. 装完 PowerShell 验证：
   ```powershell
   git --version
   ```

---

## 二、克隆仓库 + 安装依赖

用 **PowerShell（普通权限即可）** 执行：

```powershell
# 切到 C 盘根目录（或您喜欢的位置）
cd C:\

# Clone 您的仓库
git clone https://github.com/redclawcecilia-tech/family.git
cd family

# 安装 Python 依赖
python -m pip install --upgrade pip
python -m pip install imapclient
```

---

## 三、填写配置

```powershell
# 从模板复制一份
copy server\config.env.example server\config.env

# 用记事本打开编辑
notepad server\config.env
```

记事本里填入（您个人的真实值）：

```
GMAIL_USER=redclawcecilia@gmail.com
GMAIL_APP_PASSWORD=<16位应用专用密码，去掉空格>
GITHUB_USER=redclawcecilia-tech
GITHUB_REPO=family
GITHUB_TOKEN=<您的 ghp_ 开头 Token>
REPO_PATH=C:\family
```

> 真实密码和 Token 不要放进 git 跟踪的文件。`config.env` 已在 .gitignore 里，不会被提交。

> 注意 Windows 路径用反斜杠 `\`，但在 `.env` 里最好写正斜杠 `/` 或双反斜杠 `\\`（实际测试时两种都可）。最保险：`REPO_PATH=C:/family`

Ctrl+S 保存 → 关闭记事本。

---

## 四、测试手动运行

在 PowerShell 里（仍在 `C:\family` 目录）：

```powershell
python server\monitor.py
```

看到类似输出就说明成功：

```
2026-04-24 ... [INFO] 📬 启动 Gmail 监听 · 账号: redclawcecilia@gmail.com
2026-04-24 ... [INFO] 🔐 已登录 Gmail
2026-04-24 ... [INFO] 👂 IDLE 等待新邮件...
```

**测试事件驱动：** 另开一个浏览器窗口登录 Gmail，把一封旧的【基金净值】邮件标记为未读再转发给自己（或直接从已读标为未读），**5 秒内**应该看到日志：

```
[INFO] 🔔 收到 IMAP 通知: 1 个事件
[INFO] 🎯 解析到净值 2026-04-22 = 1.2966 ...
```

按 `Ctrl+C` 停止手动测试，下面把它变成后台服务。

---

## 五、装成 Windows 服务（NSSM，常驻不掉）

Windows 没有 systemd，我们用 **NSSM**（Non-Sucking Service Manager）把 Python 脚本注册成真正的 Windows 服务：开机自启、崩溃自动重启、后台运行不需要登录用户。

### 5.1 下载 NSSM

```powershell
cd C:\family
Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile nssm.zip
Expand-Archive nssm.zip -DestinationPath nssm-unpacked -Force
# 根据您的系统架构（64 位 win 现在都是）
copy nssm-unpacked\nssm-2.24\win64\nssm.exe C:\family\nssm.exe
del nssm.zip
rmdir nssm-unpacked /s /q
```

### 5.2 用 NSSM 安装服务（需要管理员）

**右键开始菜单 → "终端(管理员)" / "Windows PowerShell (管理员)"** 打开 PowerShell。

```powershell
cd C:\family

# 安装服务
.\nssm.exe install FamilyFundMonitor python C:\family\server\monitor.py

# 设置工作目录
.\nssm.exe set FamilyFundMonitor AppDirectory C:\family

# 设置日志输出（可选但推荐）
mkdir C:\family\logs -ErrorAction SilentlyContinue
.\nssm.exe set FamilyFundMonitor AppStdout C:\family\logs\monitor.log
.\nssm.exe set FamilyFundMonitor AppStderr C:\family\logs\monitor.err.log

# 崩溃后 10 秒自动重启
.\nssm.exe set FamilyFundMonitor AppRestartDelay 10000

# 启动服务
.\nssm.exe start FamilyFundMonitor
```

### 5.3 验证

```powershell
# 看服务状态
Get-Service FamilyFundMonitor
# 应显示 Status: Running

# 看实时日志
Get-Content C:\family\logs\monitor.log -Wait -Tail 20
```

出现 `🔐 已登录 Gmail` + `👂 IDLE 等待新邮件...` 即成功。关掉 PowerShell 窗口，服务会继续在后台跑。

---

## 六、日常操作

```powershell
# 查看服务状态
Get-Service FamilyFundMonitor

# 重启服务（比如改了 config.env 后）
Restart-Service FamilyFundMonitor

# 停止服务
Stop-Service FamilyFundMonitor

# 启动服务
Start-Service FamilyFundMonitor

# 看最新日志
Get-Content C:\family\logs\monitor.log -Tail 50
```

---

## 七、卸载（不用了再删）

```powershell
# 管理员 PowerShell
.\nssm.exe stop FamilyFundMonitor
.\nssm.exe remove FamilyFundMonitor confirm

# 顺便把 Gmail App Password 到 https://myaccount.google.com/apppasswords 撤销
```

---

## 故障排查

| 现象 | 可能原因 | 解决 |
|---|---|---|
| 手动运行报 `ImportError: imapclient` | 没装 imapclient | `python -m pip install imapclient` |
| 登录失败 `AUTHENTICATIONFAILED` | App Password 错误或 2FA 未开 | 重开 App Password，注意密码空格要去掉或保留原样都可 |
| `git push` 失败 | Token 过期或无 repo 权限 | 重新生成 PAT，带 `repo` 权限 |
| 服务状态是 Running 但没反应 | IDLE 被防火墙拦 | 确认服务器出站 TCP 993 没被封 |
| 中文日志乱码 | Windows 控制台编码 | `chcp 65001` 切到 UTF-8 |
