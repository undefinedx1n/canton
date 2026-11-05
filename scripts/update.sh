#!/usr/bin/env bash
# 服务器端一键更新：拉取最新代码 -> 重建镜像 -> 重启 -> 健康检查
# 用法：bash scripts/update.sh          （在 /srv/park-scheduling 下执行）
#       bash scripts/update.sh v1.0.4   （回滚到指定 tag）
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-}"

echo "==== 当前版本 ===="
git log --oneline -1

if [ -n "$TARGET" ]; then
  echo "==== 回滚到 $TARGET ===="
  git fetch --tags origin
  git checkout --detach "$TARGET"
else
  echo "==== 拉取最新代码 ===="
  git fetch --tags origin
  if ! git rev-parse --abbrev-ref HEAD | grep -q main; then
    git checkout main
  fi
  git pull --ff-only origin main
fi

echo "==== 重建镜像 ===="
docker compose build api

echo "==== 重启服务 ===="
docker compose up -d db api

echo "==== 健康检查 ===="
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health || true)
  if [ "$code" = "200" ]; then
    curl -s http://127.0.0.1:8000/api/health
    echo
    echo "==== 更新完成 ===="
    exit 0
  fi
  sleep 3
done

echo "健康检查未通过，最近日志："
docker compose logs --tail=40 api
exit 1
