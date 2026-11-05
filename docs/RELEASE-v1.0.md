# 第一版上线记录（v1.0.4）

> 本文件记录首次部署到生产服务器的实际环境、步骤与验收结果，便于复现与回滚。
> 不含任何密码、密钥等敏感信息（均在服务器 `/srv/park-scheduling/.env` 与 `/root/.scheduler-admin-pwd`，权限 600）。

## 1. 版本信息

| 项 | 值 |
|---|---|
| 版本号 | v1.0.4 |
| 首次上线时间 | 2026-11-05 18:21 (UTC+8) |
| 代码来源 | 本地 Git 仓库 `main` 分支，发布包 `git archive` 导出 |
| 仓库 | https://github.com/undefinedx1n/canton |
| 数据库 | PostgreSQL 16（Docker 卷 `park-scheduling_pgdata`） |
| 求解器 | OR-Tools CP-SAT 9.12 |

## 2. 服务器环境

| 项 | 值 |
|---|---|
| 公网 IP | 8.217.198.163（阿里云香港） |
| 系统 | Alibaba Cloud Linux 3 (OpenAnolis) |
| 规格 | 2 vCPU / 1.8 GiB 内存 / 40 GiB 磁盘（已有 2 GiB swap） |
| 容器 | Docker 26.1.3 + Docker Compose v2.27.0 |
| 反向代理 | 宿主机既有 nginx（80/443 已被占用，含 `api.1gavin.online` 站点） |

## 3. 部署架构

```
公网 :80  ->  nginx (conf.d/scheduler.conf, server_name 8.217.198.163)
                 └─> 127.0.0.1:8000  api 容器 (FastAPI + 静态前端)
                        └─> db 容器 postgres:16-alpine (仅容器网络, 不暴露公网)
```

- 未使用 Compose 里的 Caddy（`profiles: ["tls"]`，仅在拥有域名且 80/443 空闲时启用），避免与既有 nginx 抢占端口。
- 应用容器只绑定 `127.0.0.1:8000`，公网仅能通过 nginx 访问。

## 4. 实际部署步骤

```bash
# 1) 本地导出发布包（只含 Git 跟踪文件）
git archive --format=tar.gz --prefix=park-scheduling/ -o canton-v1.0.4.tar.gz HEAD

# 2) 上传并解压
scp canton-v1.0.4.tar.gz root@8.217.198.163:/tmp/
tar xzf /tmp/canton-v1.0.4.tar.gz -C /srv

# 3) 生成 .env（脚本自动生成随机密码，仅写一次）
cd /srv/park-scheduling && bash /root/deploy_v1.sh

# 4) 构建并启动（db + api，不含 caddy）
docker compose build api && docker compose up -d db api

# 5) 初始化基础数据（幂等，不含示例员工）
docker compose exec -T api python /app/scripts/init_base.py

# 6) nginx 反代
cp deploy/nginx-ip.conf /etc/nginx/conf.d/scheduler.conf
nginx -t && systemctl reload nginx
```

回滚：`git checkout v1.0.0 && docker compose up -d --build api`（镜像层保留，约 1 分钟）。
nginx 配置备份位于 `/root/nginx-backup-20261105-180326/`。

## 5. 上线验收结果

| 验收项 | 结果 | 说明 |
|---|---|---|
| `GET /api/health` | 通过 | `{"status":"ok","solver":"cp-sat"}` |
| 首页静态资源 | 通过 | HTTP 200，5.8 KB HTML，静态导出由后端托管 |
| 管理员登录 | 通过 | 返回 JWT，role=admin |
| 基础数据 | 通过 | 12 个岗位 / 48 条需求模板 / 3 个运营角色 / 64 天日历 |
| 建员工 → 排班 | 通过 | 6 名测试员工，CP-SAT 求解 95 ms，状态 OPTIMAL |
| 缺口诊断 | 通过 | 6 人无法覆盖 73 人次需求，明确报告 55 人次缺口与逐岗位缺口清单 |
| 发布版本 | 通过 | `status=published`，写入审计日志 |
| Excel 导出 | 通过 | 12 265 字节，表头/日期列/班次/运营角色标注均正确 |
| 三档视口巡检 | 通过 | 390 / 820 / 1440 宽共 30 次页面加载，横向溢出 0 px，无 JS 报错 |
| 后端测试 | 通过 | `98 passed, 1 xfailed`（本地 SQLite，虚构数据） |

测试数据已全部清理，交付状态为：0 名员工、12 个岗位、48 条模板、1 个管理员账号。

## 6. 上线过程中发现并修复的缺陷

| 编号 | 现象 | 原因 | 修复 |
|---|---|---|---|
| D-03 | 发布 11/6–11/8 班表后，首页仍显示「还没有排班数据」 | 前端固定传 `start=今天`，后端严格按「覆盖今天」查询，而排班通常提前一天生成 | 前端不再传日期；后端 `GET /api/schedule/current` 增加回退链：覆盖今天 → 最近未来版本 → 最新历史版本 → 404，并补 6 个回归测试 |

## 7. 已知限制与后续建议

1. **没有 HTTPS**：当前按 IP + HTTP 访问，登录口令在公网明文传输。建议尽快绑定域名并启用证书（`deploy/Caddyfile` + `--profile tls`，或在 nginx 加 443 server 块）。
2. **管理员初始密码**：随机生成，存放在服务器 `/root/.scheduler-admin-pwd`。系统暂未提供在线改密入口，如需更换可执行：
   ```bash
   docker compose exec -T api python - <<'PY'
   # 见 backend/app/security.py hash_password
   PY
   ```
   （后续版本应补「修改密码」页面。）
3. **备份未配置定时任务**：`scripts/backup.sh` 已就绪，需在服务器 `crontab` 添加上午 02:30 的每日备份（见 DEPLOY.md 第 4 节）。
4. **单机单副本**：2 vCPU / 1.8 GiB，求解 3 天班表约 95 ms；若扩展到 14 天 + 全量员工，建议观察 `schedule_runs.duration_ms` 并调大 `SOLVER_MAX_TIME_SECONDS`。
5. **真实数据未导入**：上线后需在「员工档案」下载模板、导入真实名单，再核对「每日需求」，才能生成正式班表。
