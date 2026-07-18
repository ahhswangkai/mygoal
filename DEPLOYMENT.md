# 服务器部署

适用于 Ubuntu/Debian 服务器。服务器需要预先安装：

- Git
- Python 3、`python3-venv`
- Node.js 18+、npm
- Nginx
- MongoDB（本机或远程）

首次部署：

```bash
git clone -b codex/match-analysis-integration git@github.com:ahhswangkai/mygoal.git
cd mygoal
chmod +x deploy.sh
./deploy.sh
```

脚本默认域名为 `mygoal.top`。使用其他域名时：

```bash
DOMAIN=example.com ./deploy.sh
```

`DOMAIN` 不需要写 `http://`；即使传入，脚本也会自动去除协议。

以后更新只需在项目目录重新运行：

```bash
./deploy.sh
```

使用远程 MongoDB 时，首次部署后编辑 `.env`：

```bash
MONGODB_URI=mongodb://用户名:密码@数据库地址:27017/football_data
```

然后重新运行 `./deploy.sh`。

配置火山方舟 Skill AI 分析时，编辑 `.env`：

```bash
ARK_API_KEY=你的火山方舟APIKey
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=你在方舟控制台开通的模型ID
AI_REQUEST_TIMEOUT=90
AI_MAX_RETRIES=1
AI_MIN_REFRESH_SECONDS=300
```

API Key 只保存在服务器 `.env`，不要提交到 Git。修改后重启后端：

```bash
sudo systemctl restart mygoal
```

运行时 Skills 位于 `ai_skills/*/SKILL.md`。生成分析时，后端会根据比赛实际可用的数据自动选择相关 Skills，并将结果缓存到 MongoDB 的 `ai_analyses` 集合。

常用命令：

```bash
sudo systemctl status mygoal
sudo journalctl -u mygoal -f
sudo systemctl restart mygoal
sudo nginx -t
```

配置 HTTPS（域名已解析到服务器后）：

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d mygoal.top --redirect
sudo systemctl enable --now certbot.timer
sudo certbot renew --dry-run
```

部署脚本会检测 `/etc/letsencrypt/live/mygoal.top/`。证书存在时会自动保留 HTTPS 和 HTTP 跳转配置，不会在下次部署时覆盖掉 HTTPS。
