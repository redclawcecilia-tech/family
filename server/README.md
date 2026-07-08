# 方舟云服务器部署指南

**完全脱离 Cowork / GitHub Actions 的事件驱动净值监听**

架构：邮箱 IMAP IDLE（长连接；不支持时自动轮询）→ 青琰邮件一到立即触发 → 改 HTML → git push → Cloudflare 自动部署

---

## 一、准备邮箱授权码（3 分钟）

网易邮箱：网页版邮箱 → 设置 → POP3/SMTP/IMAP → 开启 IMAP → 生成授权码。

Gmail：打开 https://myaccount.google.com/apppasswords 生成 App Password（国内服务器通常还需要 SOCKS5 代理）。

> 授权码只给此服务用，随时可以在邮箱设置里撤销。IMAP 登录时密码里的空格可忽略。

---

## 二、在服务器上部署（5 分钟）

假设您用 SSH 登进方舟云服务器，用户为 `cecilia`（请替换为实际用户名）。

```bash
# 1. 安装系统依赖
sudo apt update
sudo apt install -y python3 python3-pip git

# 2. Clone 仓库到 home 目录
cd ~
git clone https://github.com/redclawcecilia-tech/family.git
cd family

# 3. 安装 Python 依赖
pip3 install --user -r server/requirements.txt

# 4. 配置环境变量
cp server/config.env.example server/config.env
nano server/config.env   # 填入邮箱账号、授权码、GitHub Token
chmod 600 server/config.env

# 5. 手动跑一次测试（用环境文件）
set -a && source server/config.env && set +a
python3 server/monitor.py
# 看到 "🔐 已登录邮箱" 和 "👂 IDLE 等待新邮件..." 就说明正常
# Ctrl+C 停止
```

如果测试成功，安装为 systemd 服务让它常驻：

```bash
# 6. 编辑 service 文件里的路径和用户名
nano server/family-fund-monitor.service
# 改 User=, Group=, WorkingDirectory=, EnvironmentFile=, ExecStart= 里的路径

# 7. 复制到 systemd 并启动
sudo cp server/family-fund-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now family-fund-monitor

# 8. 查看运行状态
sudo systemctl status family-fund-monitor
# 看日志
sudo journalctl -u family-fund-monitor -f
```

---

## 三、测试

部署好之后，下一次青琰发净值邮件到监听邮箱，**秒级**内：
- 服务器日志出现 `🎯 解析到净值 ...`
- GitHub 仓库多一个 "净值自动更新" commit
- Cloudflare Pages 自动部署
- 父母打开 `https://family.redclawcecilia.workers.dev` 就是最新数据

**手动测试：** 您可以自己转发一封旧的【基金净值】邮件给监听邮箱，服务应当立即检测到并处理。

---

## 四、故障排查

- **登录失败**：授权码是否正确？IMAP 是否已开启？Gmail 是否已配置代理？
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

在邮箱设置里撤销授权码即可彻底断开访问。
