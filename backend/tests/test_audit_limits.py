"""审计日志字段长度约束。

线上踩过的坑：批量删除 27 人时，把 ID 列表直接拼进 `audit_logs.entity_id`
（VARCHAR(64)）→ PostgreSQL 抛 StringDataRightTruncation → 整个事务回滚 → 接口 500。
本地测试用 SQLite，**不校验 VARCHAR 长度**，所以单靠"接口返回 200"并不能保证
生产可用；这里显式断言落库字符串不超列宽。
"""
from __future__ import annotations

from app.deps import AUDIT_CODE_WIDTH, clip_code, pack_ids, write_audit
from app.models import AuditLog, Employee


def test_pack_ids_keeps_short_list_verbatim():
    assert pack_ids([3, 5, 8]) == "3,5,8"


def test_pack_ids_compresses_long_list_within_width():
    ids = list(range(1, 41))  # 直接拼接约 130 字符，必然溢出
    packed = pack_ids(ids)

    assert len(packed) <= AUDIT_CODE_WIDTH
    assert packed.startswith("1,2,3")
    assert "…(+" in packed
    # 省略号里的数量要和被省略的 ID 数对得上
    omitted = int(packed.split("…(+")[1].rstrip(")"))
    assert omitted == len(ids) - (packed.count(",") - 0) - 1


def test_clip_code_truncates_with_ellipsis():
    assert clip_code("abc") == "abc"
    clipped = clip_code("x" * 200)
    assert len(clipped) == AUDIT_CODE_WIDTH
    assert clipped.endswith("…")


def test_write_audit_clips_overlong_entity_id(db):
    before = db.query(AuditLog).count()
    write_audit(db, "u" * 200, "a" * 200, "e" * 200, ",".join(str(i) for i in range(1, 60)), "说明")
    db.commit()

    db.expire_all()
    assert db.query(AuditLog).count() == before + 1
    row = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert len(row.actor) <= AUDIT_CODE_WIDTH
    assert len(row.action) <= AUDIT_CODE_WIDTH
    assert len(row.entity) <= AUDIT_CODE_WIDTH
    assert len(row.entity_id) <= AUDIT_CODE_WIDTH


def test_batch_purge_many_employees_writes_short_audit(client, db):
    """回归：27 人量级的批量删除不能因为审计字段超长而失败。"""
    emps = [Employee(name=f"批量删除压测{i:02d}", employment_type="long_term") for i in range(27)]
    db.add_all(emps)
    db.commit()
    ids = [e.id for e in emps]

    resp = client.post("/api/employees/batch-delete", json={"employee_ids": ids, "mode": "purge"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["purged"] == 27

    db.expire_all()
    row = db.query(AuditLog).filter(AuditLog.action == "batch_purge").order_by(AuditLog.id.desc()).first()
    assert row is not None
    assert len(row.entity_id) <= AUDIT_CODE_WIDTH
