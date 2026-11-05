# 部署与运维说明

目标环境：阿里云香港 Linux（Ubuntu 22.04 / Debian 12），Docker + Docker Compose，Caddy 自动 HTTPS。
本地开发与生产使用同一套 Compose 服务定义，仅环境变量不同。

---

## 1. 服务器准备

```bash
# 非 root 部署账号
adduser deploy && usermod -aG sudo deploy
# 仅允许密钥登录
# /etc/ssh/sshd_config: PasswordAuthentication no, PermitRootLogin no

# 防火墙：只开放 22 / 80 / 443
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable

# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
```

域名解析：`schedule.example.com A <服务器公网IP>`。

## 2. 首次部署

```bash
git clone <repo> /srv/park-scheduling && cd /srv/park-scheduling

cp .env.example .env
# 必须修改：
#   JWT_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
#   BOOTSTRAP_ADMIN_PASSWORD=<强密码>
#   POSTGRES_PASSWORD=<强密码>
vi .env

# Caddy 域名
vi deploy/Caddyfile     # 把 schedule.example.com 换成真实域名

docker compose up -d --build
docker compose ps
curl -fsS https://schedule.example.com/api/health
```

启动时自动建表并创建管理员账号（仅当 users 表为空）。生产建议改用迁移：

```bash
# 在 api 容器内执行（首次可跳过，因为启动会 create_all）
docker compose exec api alembic -c /app/backend/alembic.ini upgrade head
```

> 后续版本升级：先 `upgrade head` 再滚动重启 api，避免结构与代码不匹配。

### 2.1 无域名场景（IP 直连，复用宿主机已有 nginx）

服务器上 80/443 已被其他站点（nginx）占用、且暂时没有可解析域名时：

```bash
# 1) 不让 Caddy 抢占端口：compose 里 caddy 已设置 profiles: ["tls"]，默认不启动
docker compose build api
docker compose up -d db api          # 只启动数据库与 api，端口仅绑 127.0.0.1:8000

# 2) 初始化基础数据（幂等，只建岗位/需求模板/运营角色/日历，不建示例员工）
docker compose exec -T api python /app/scripts/init_base.py

# 3) 用宿主机 nginx 反代（server_name 改为真实公网 IP）
sudo cp deploy/nginx-scheduler.conf /etc/nginx/conf.d/scheduler.conf
sudo nginx -t && sudo systemctl reload nginx
```

校验：`curl -s http://<公网IP>/api/health`。改动 nginx 前先备份 `/etc/nginx/conf.d`。

### 2.2 绑定域名 + HTTPS（当前生产做法）

生产环境已绑定 `canton.1gavin.online`，证书由 Let's Encrypt 签发（certbot webroot 方式，
不复用 `--nginx` 插件，避免改动同机其他站点）。`deploy/nginx-scheduler.conf` 即线上配置：
域名 80 → 301 跳 HTTPS，443 反代 `127.0.0.1:8000`，另保留 IP 直连入口便于运维排查。

首次签发（证书不存在时先只装 80 段，见 `deploy/nginx-scheduler.conf` 注释）

```bash
# 1) 准备 ACME 校验目录并放行 80
sudo mkdir -p /var/www/letsencrypt

# 2) 签发证书
sudo certbot certonly --webroot -w /var/www/letsencrypt \
     -d canton.1gavin.online --non-interactive --agree-tos

# 3) 安装完整配置（含 443），先测再 reload
sudo cp deploy/nginx-scheduler.conf /etc/nginx/conf.d/scheduler.conf
sudo nginx -t && sudo systemctl reload nginx
```

续期：`certbot-renew.timer` 每日检查，续期后由
`/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` 自动 `reload nginx`。
验证续期链路：

```bash
sudo certbot renew --cert-name canton.1gavin.online --dry-run
```

常见坑：

- **`duplicate listen options for [::]:443`**：同机已有站点声明了 `ipv6only=on`，本机 IPv6 段不能再重复声明，
  配置里 IPv6 只写 `listen [::]:443 ssl;`。
- **`protocol options redefined`**：同一 socket 上 HTTP/2 声明不一致，保持与既有站点一致或只在 IPv4 开 `http2`。
- 域名解析改到本机后，若本机浏览器走代理/DNS 被劫持，可用
  `HOST_RESOLVER="MAP canton.1gavin.online <IP>"` 跑 `frontend/ui-audit.mjs` 验证，或 `curl --resolve`。

> 如后续改用 Caddy（`docker compose --profile tls up -d caddy`），需停用本节 nginx 站点，避免抢 80/443。


## 3. 初始化业务数据

1. 浏览器登录 → 「员工档案」→ 下载模板 → 填写真实名单 → **导入预览** → 确认导入。
2. 「岗位与需求」核对 12 个岗位的资格限制与四类日期模板。
3. 「每日需求」确认未来三天的日期类型（节假日/旺季）与人数。
4. 「请假审批」录入已批准的请假。
5. 「自动排班」生成 → 「人工调整」微调 → 「发布班表」→ 导出 Excel 发于微信群。

生产环境切勿直接用演示数据：可执行
`docker compose exec api python /app/scripts/init_demo.py --force` 仅用于验收演练，之后重新导入真实数据。

## 4. 备份

```bash
# 每日 02:30 备份，保留 30 份
crontab -e
30 2 * * * cd /srv/park-scheduling && DATABASE_URL="postgresql://schedule:PWD@127.0.0.1:5432/schedule" BACKUP_DIR=/srv/backups ./scripts/backup.sh >> /var/log/schedule-backup.log 2>&1
```

异地副本（示例）：

```bash
30 3 * * * rsync -az /srv/backups/ backup@another-host:/backups/park-scheduling/
```

## 5. 恢复

```bash
docker compose stop api
./scripts/restore.sh /srv/backups/schedule-20261105-023000.sql.gz
docker compose start api
curl -fsS https://schedule.example.com/api/health
```

恢复后校验：登录 → 「版本历史」确认最新发布版本、抽查某日排班与统计人数。

## 6. 日常更新与版本回滚

服务器部署目录已接入 Git（`origin` = GitHub 仓库），更新只需一条命令：

```bash
cd /srv/park-scheduling
bash scripts/update.sh              # 拉取 main 最新代码 -> 重建镜像 -> 重启 -> 健康检查
bash scripts/update.sh v1.0.4       # 回滚到指定版本标签（detached 检出新旧代码后重建）
```

脚本内部会校验 `/api/health` 返回 200，失败时打印最近 40 行容器日志并以非 0 退出。

手动回滚（不使用脚本时）：

```bash
git log --oneline -5
git checkout <上一个可用提交>
docker compose build api
docker compose up -d api
# 若本次升级包含数据库迁移，需评估是否 downgrade；建议升级前先备份
```

> 更新前建议先备份（见第 4 节）。`.env`、数据卷、`/root/nginx-backup-*` 不受 Git 操作影响。

## 7. 日志与监控

- 容器日志已配置轮转（`max-size=10m`，`max-file=5`）：`docker compose logs -f api`。
- Caddy 访问日志：`/data/access.log`（volume `caddy_data`）。
- 健康检查：`GET /api/health` 返回 `{"status":"ok","solver":"cp-sat"}`，Compose 已内置 HEALTHCHECK。
- 排班求解耗时记录在 `schedule_runs.duration_ms`，超时（默认 20s）会返回当前最佳解。

## 8. 安全清单

- [x] 仅 HTTPS 入口，HTTP 自动跳转（Caddy 默认）
- [x] 数据库端口不对公网暴露（compose 中 `expose` 而非 `ports`）
- [x] JWT_SECRET / 初始密码走环境变量，不入库不入 Git
- [x] 密码 PBKDF2 存储，接口全部鉴权
- [x] SQL 注入防护（SQLAlchemy 参数化）
- [x] 导入预览机制，避免误覆盖
- [x] 审计日志记录发布、人工调整、配置变更
- [ ] 生产建议加：登录失败限流、双因素、数据库按日全量校验

## 9. 常见问题

| 现象 | 排查 |
|---|---|
| 页面 404 或空白 | `frontend/out/index.html` 是否存在；容器内 `/app/frontend/out` 是否被复制 |
| 登录 401 | `.env` 中 `JWT_SECRET` 是否变更导致旧 token 失效；重新登录 |
| 求解器变慢 | 调大 `SOLVER_MAX_TIME_SECONDS`；减少同时候选岗位；检查是否有大量搭班偏好 |
| 发布被阻止 | 「人工调整」页查看校验问题；资格/请假/主管角色/超编为硬错误 |
| 缺口较多 | 检查「每日需求」目标是否高于合格人力，或兼职/实习排班偏好是否过于集中 |
