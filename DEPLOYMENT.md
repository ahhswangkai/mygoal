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

配置 Football AI Engine（FAE）的方舟说明层时，编辑 `.env`：

```bash
ARK_API_KEY=你的火山方舟APIKey
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
ARK_MODEL=ark-code-latest
ARK_API_MODE=chat_completions
AI_REQUEST_TIMEOUT=90
AI_MAX_RETRIES=1
AI_MIN_REFRESH_SECONDS=300
FAE_AUTO_ANALYZE=true
FAE_AUTO_NARRATIVE=false
FAE_AUTO_REFRESH_MINUTES=60
FAE_LEARNING_MIN_SAMPLES=10
FAE_SKILL_MIN_NEW_SAMPLES=10
FAE_ADMIN_USERNAMES=你的登录用户名
FAE_DAILY_AI_ENABLED=true
FAE_DAILY_AI_HOUR=12
FAE_DAILY_AI_MINUTE=10
FAE_DAILY_AI_BATCH_SIZE=1
FAE_DAILY_AI_TIMEOUT=180
FAE_DAILY_AI_MAX_TOKENS=4096
FAE_DAILY_AI_MAX_RETRIES=0
FAE_DAILY_AI_THINKING=disabled
```

API Key 只保存在服务器 `.env`，不要提交到 Git。修改后重启后端：

```bash
sudo systemctl restart mygoal
```

FAE 会自动运行确定性盘口分类、八维评分、概率、推荐和风险控制，并将结果保存到 MongoDB 的 `fae_analyses` 集合。历史版本写入 `fae_analysis_history`，赛后复盘写入 `fae_reviews`。复盘证据先生成待验证的 Skill 候选，通过推荐页发布后才更新线上参数。

每日全日 AI 研判会先把同一天每场未开赛比赛分别交给火山方舟，按欧赔、亚盘升深、竞彩让球、大小球、市场一致性五项分析，再单独调用一次全日汇总，生成排名和2/3关组合。总览保存到 `fae_daily_ai_runs`，每次运行的逐场结论和赔率快照独立保存到 `fae_daily_ai_matches`。

只有“全部比赛均未开赛”时生成的运行才可用于正式复盘。赛后系统每15分钟按该不可变快照结算 AI 主玩法、2串1、3串1、赔率、收益率及模型一致性冲突，结果保存到 `fae_daily_ai_reviews`。AI 主复盘优先驱动平/让平 Skill 候选；旧 `fae_draw_reviews` 仅保留作历史对照。火山结论与确定性概率严重冲突时会保留原始选择，但正式推荐和复盘使用一致性护栏后的有效选择。

- `FAE_AUTO_ANALYZE=true`：每次定时抓取后自动更新未开赛比赛。
- `FAE_AUTO_NARRATIVE=false`：自动任务默认不调用大模型，避免重复消耗；手动重新运行时仍可调用方舟生成说明。
- `FAE_AUTO_REFRESH_MINUTES=60`：同一场比赛自动更新的最短间隔。
- `FAE_LEARNING_MIN_SAMPLES=10`：规则至少达到该总复盘样本数，才允许生成候选。
- `FAE_SKILL_MIN_NEW_SAMPLES=10`：每次 Skill 发布后还需积累的新样本数，避免重复使用同一批赛果升级。
- `FAE_ADMIN_USERNAMES`：允许生成、发布和回滚 Skill 的登录用户名，多个账号使用英文逗号分隔。
- `FAE_DAILY_AI_ENABLED=true`：开启每日火山全日研判定时任务。
- `FAE_DAILY_AI_HOUR/MINUTE`：每天运行时间，默认北京时间 `12:10`。
- `FAE_DAILY_AI_BATCH_SIZE=1`：每场独立调用并保存检查点，全部完成后再调用一次短请求汇总全日排名和混合组合。
- `FAE_DAILY_AI_TIMEOUT=180`：全日详细分析单批最长等待秒数。
- `FAE_DAILY_AI_MAX_TOKENS=4096`：限制单批输出长度，防止超长响应。
- `FAE_DAILY_AI_MAX_RETRIES=0`：全日任务失败时不自动重复扣费，由下一次调度或管理账号手动重跑。
- `FAE_DAILY_AI_THINKING=disabled`：关闭隐藏深度思考，把输出预算用于可核验的五维结论和JSON。
- 同一天赔率快照没有变化时自动返回缓存，不重复调用模型；推荐页管理账号可强制重新研判。

`/api/coding/v3` 使用 OpenAI 兼容的 Chat Completions 协议。若改用普通火山方舟模型 API，可将 `ARK_BASE_URL` 改为普通方舟地址，并把 `ARK_API_MODE` 改为 `responses`。

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
