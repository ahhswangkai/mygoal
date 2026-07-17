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
DOMAIN=你的域名 ./deploy.sh
```

没有域名时：

```bash
./deploy.sh
```

以后更新只需在项目目录重新运行：

```bash
./deploy.sh
```

使用远程 MongoDB 时，首次部署后编辑 `.env`：

```bash
MONGODB_URI=mongodb://用户名:密码@数据库地址:27017/football_data
```

然后重新运行 `./deploy.sh`。

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
sudo certbot --nginx -d 你的域名
```
