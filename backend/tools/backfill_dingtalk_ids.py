"""把钉钉用户ID按姓名回填到员工档案（一次性的对接动作，可重复执行）。

背景：导出「柯达广州塔排班表」需要「用户ID」列（钉钉成员 ID），系统里原本没有这个字段。
数据来源是人力给的那份模板：姓名 + 用户ID 两列。
这里只写 `external_user_id`，不动其他档案字段；同名不猜，列进 ambiguous 由人工处理。

用法：
    ../.venv/Scripts/python.exe tools/backfill_dingtalk_ids.py            # 用默认 JSON + 线上地址
    API=https://canton.1gavin.online ... tools/backfill_dingtalk_ids.py tools/dingtalk_ids.json
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

DEFAULT_API = os.environ.get("API", "https://canton.1gavin.online")
USERNAME = os.environ.get("API_USER", "admin")
PASSWORD = os.environ.get("API_PASSWORD", "")


def _post(url: str, payload, token: str | None = None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def main() -> int:
    mapping_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("dingtalk_ids.json")
    rows = json.loads(mapping_path.read_text(encoding="utf-8"))
    if not PASSWORD:
        print("请先设置 API_PASSWORD 环境变量（管理员密码）")
        return 2
    token = _post(f"{DEFAULT_API}/api/auth/login", {"username": USERNAME, "password": PASSWORD})["access_token"]
    result = _post(f"{DEFAULT_API}/api/employees/external-ids", rows, token)
    print(f"提交 {len(rows)} 条 → 更新 {result['updated']} 人")
    if result["missing"]:
        print(f"系统里没有这些姓名（未写入）：{'、'.join(result['missing'])}")
    if result["ambiguous"]:
        print(f"系统内有重名（未写入，请人工处理）：{'、'.join(result['ambiguous'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
