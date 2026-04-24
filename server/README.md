# 方舟云服务器部署指南

**完全脱离 Cowork / GitHub Actions 的事件驱动净值监听**

架构：Gmail IMAP IDLE（长连接）→ 青琰邮件一到立即触发 → 改 HTML → git push → Cloudflare 自动部署

---

## 一、在 Gmail 开启应用专用密码（3 分钟）

1. 打开 https://myaccount.google.com/security
2. 确保 **"2步验证"** 已开启（没开就先开，用手机验证）
3. 打开 https://myaccount.google.com/apppasswords
4. App name 填 `family-fund-monitor` → Create
5. 会显示一个 **16 位密码**（形如 `abcd efgh ijkl mnop`），**复制保存**

> 这个密码**只给此服务用**，您随时可以在同一页面撤销它。IMAP 登录时密码里的空格可忽略。

---

## 二、在服务器上部署（5 分钟）

假设您用 SSH 登进方舟云服务器，用户为 `cecilia`（请替换为实际用户名）。

```bash
# 1. 安装依赖
sudo apt update
sudo apt install -y python3 python3-pip git
pip3 install --user imapclient

# 2. Clone 仓库到 home 目录
cd ~
git clone https://github.com/redclawcecilia-tech/family.git
cd family

# 3. 配置环境变量
cp server/config.env.example server/config.env
nano server/config.env   # 填入 Gmail 账号、App Password、GitHub Token
chmod 600 server/config.env

# 4. 手动跑一次测试（用环境文件）
set -a && source server/config.env && set +a
python3 server/monitor.py
# 看到 "🔐 已登录 Gmail" 和 "👂 IDLE 等待新邮件..." 就说明正常
# Ctrl+C 停止
```

如果测试成功，安装为 systemd 服务让它常驻：

```bash
# 5. 编辑 service 文件里的路径和用户名
nano server/family-fund-monitor.service
# 改 User=, Group=, WorkingDirectory=, EnvironmentFile=, ExecStart= 里的路径

# 6. 复制到 systemd 并启动
sudo cp server/family-fund-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now family-fund-monitor

# 7. 查看运行状态
sudo systemctl status family-fund-monitor
# 看日志
sudo journalctl -u family-fund-monitor -f
```

---

## 三、测试

部署好之后，下一次青琰发净值邮件到您 Gmail，**秒级**内：
- 服务器日志出现 `🎯 解析到净值 ...`
- GitHub 仓库多一个 "净值自动更新" commit
- Cloudflare Pages 自动部署
- 父母打开 `https://family.redclawcecilia.workers.dev` 就是最新数据

**手动测试：** 您可以自己转发一封旧的【基金净值】邮件给自己（同一个 Gmail 收件箱），服务应当立即检测到并处理。

---

## 四、故障排查

- **登录失败**：App Password 是否正确？是否开了 2FA？密码里的空格可以保留或去掉都行。
- **找不到邮件**：`server/monitor.py` 里的搜索条件是 `SUBJECT SXR047`，如果您的净值邮件主题格式变了要改。
- **git push 失败**：`config.env` 里的 `GITHUB_TOKEN` 是否有 `repo` 权限？是否过期？
- **IDLE 断开**：正常，每 25 分钟会主动重连一次，systemd 也会在异常时自动重启。

---

## 五、停用/卸载

```bash
sudo systemctl disable --now family-fund-monitor
sudo rm /etc/systemd/system/family-fund-monitor.service
sudo systemctl daemon-reload
```

在 Gmail 账号页面（apppasswords）撤销那个 App Password 即可彻底断开访问。
