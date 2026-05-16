# Windows 服务器部署指南

方舟云 Windows 服务器专用。如果您的服务器是 Linux，请看 `../README.md`。

## 准备工作

1. **远程桌面**登录到您的 Windows 服务器（用方舟云控制台或 RDP 客户端）
2. 准备好网易邮箱授权码（不是登录密码！获取方式：网易邮箱网页版 → 设置 → POP3/SMTP/IMAP → 开启 IMAP → 生成授权码）
3. 准备好 GitHub Personal Access Token（带 `repo` 权限）

---

## 一、安装 Python + Git（10 分钟）

### 1.1 安装 Python

1. 浏览器打开 https://www.python.org/downloads/
2. 下载 Windows installer
3. 双击安装包
4. **⚠️ 重要：** 安装第一个页面底部必须勾 ✅ **"Add python.exe to PATH"**
5. 点 "Install Now"
6. 装完开一个 **PowerShell** 窗口，输入：
   ```powershell
   python --version
   ```

### 1.2 安装 Git

1. 浏览器打开 https://git-scm.com/download/win
2. 下载 64-bit Git for Windows Setup
3. 双击安装，一路点 Next 即可
4. 装完 PowerShell 验证：
   ```powershell
   git --version
   ```

---

## 二、克隆仓库 + 安装依赖

用 **PowerShell（普通权限即可）** 执行：

```powershell
cd C:\

git clone https://github.com/redclawcecilia-tech/family.git
cd family

python -m pip install --upgrade pip
python -m pip install -r server/requirements.txt
```

---

## 三、填写配置

```powershell
copy server\config.env.example server\config.env
notepad server\config.env
```

填入真实值：

```
IMAP_SERVER=imap.163.com
IMAP_PORT=993
IMAP_USER=你的邮箱@163.com
IMAP_PASSWORD=你的授权码
GITHUB_USER=redclawcecilia-tech
GITHUB_REPO=family
GITHUB_TOKEN=<您的 ghp_ 开头 Token>
REPO_PATH=C:/family
```

> 真实密码和 Token 不要放进 git 跟踪的文件。`config.env` 已在 .gitignore 里，不会被提交。

Ctrl+S 保存 → 关闭记事本。

---

## 四、测试手动运行

```powershell
python server\monitor.py
```

看到类似输出就说明成功：

```
2026-05-10 ... [INFO] 📬 启动净值监听 · 邮箱: redclawcecilia@163.com
2026-05-10 ... [INFO] 🔐 已登录邮箱 redclawcecilia@163.com
2026-05-10 ... [INFO] 👂 IDLE 等待新邮件...
```

**测试事件驱动：** 从另一个邮箱给这个 163 邮箱发一封主题包含"基金净值"的邮件，**5 秒内**应该看到日志更新。

按 `Ctrl+C` 停止手动测试，下面把它变成后台服务。

---

## 五、装成 Windows 服务（NSSM，常驻不掉）

Windows 没有 systemd，我们用 **NSSM**（Non-Sucking Service Manager）把 Python 脚本注册成真正的 Windows 服务：开机自启、崩溃自动重启、后台运行不需要登录用户。

### 5.1 下载 NSSM

```powershell
cd C:\family
Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile nssm.zip
Expand-Archive nssm.zip -DestinationPath nssm-unpacked -Force
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

# 设置日志输出
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
Get-Service FamilyFundMonitor
# 应显示 Status: Running

Get-Content C:\family\logs\monitor.log -Wait -Tail 20
```

出现 `🔐 已登录邮箱` + `👂 IDLE 等待新邮件...` 即成功。关掉 PowerShell 窗口，服务会继续在后台跑。

---

## 六、装 web.py 成 Windows 服务（让父母能访问报告）

```powershell
cd C:\family

.\nssm.exe install FamilyFundWeb python C:\family\server\web.py
.\nssm.exe set FamilyFundWeb AppDirectory C:\family
.\nssm.exe set FamilyFundWeb AppStdout C:\family\logs\web.log
.\nssm.exe set FamilyFundWeb AppStderr C:\family\logs\web.err.log
.\nssm.exe set FamilyFundWeb AppRestartDelay 5000

.\nssm.exe start FamilyFundWeb

# 放通 Windows 防火墙 8080 端口
New-NetFirewallRule -DisplayName "FamilyFund Web 8080" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow

# 验证
Get-Service FamilyFundWeb
netstat -an | findstr 8080
```

**云控制台防火墙：** 安全组 → 入方向放通 TCP **8080**，源 `0.0.0.0/0`。

---

## 七、日常操作

```powershell
# 查看服务状态
Get-Service FamilyFundMonitor, FamilyFundWeb

# 重启 monitor（比如改了 config.env 或代码后）
Restart-Service FamilyFundMonitor

# 重启 web
Restart-Service FamilyFundWeb

# 看最新日志
Get-Content C:\family\logs\monitor.log -Tail 50
Get-Content C:\family\logs\web.log -Tail 50
```

---

## 八、卸载（不用了再删）

```powershell
.\nssm.exe stop FamilyFundMonitor
.\nssm.exe remove FamilyFundMonitor confirm
.\nssm.exe stop FamilyFundWeb
.\nssm.exe remove FamilyFundWeb confirm
```

---

## 工作原理

```
网易邮箱收到【基金净值】邮件
        ↓
IMAP IDLE 推送通知（实时，非轮询）
        ↓
monitor.py 解析净值数据
        ↓
更新 index.html
        ↓
git commit + push 到 GitHub
        ↓
Cloudflare Pages 自动部署（30-60秒）
```

---

## 故障排查

| 现象 | 可能原因 | 解决 |
|---|---|---|
| `ImportError: imapclient` | 没装依赖 | `pip install -r server/requirements.txt` |
| 登录失败 `AUTHENTICATIONFAILED` | 授权码错误或 IMAP 未开启 | 网易邮箱设置 → 开启 IMAP → 重新生成授权码 |
| 连接超时 | 服务器出站 993 端口被封 | 检查云服务器安全组出站规则 |
| `git push` 失败 | Token 过期或无 repo 权限 | 重新生成 PAT，带 `repo` 权限 |
| 服务 Running 但没反应 | IDLE 连接断开 | `Restart-Service FamilyFundMonitor` |
| 中文日志乱码 | Windows 控制台编码 | `chcp 65001` 切到 UTF-8 |
