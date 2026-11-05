"""依赖注入：数据库会话与当前用户。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或登录已过期")
    try:
        payload = decode_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态无效，请重新登录") from exc
    username = payload.get("sub")
    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


# AuditLog 里除 summary 外的字符串列都是 VARCHAR(64)。SQLite 不校验长度，
# PostgreSQL 会直接报 StringDataRightTruncation，导致整个事务回滚（线上 500）。
AUDIT_CODE_WIDTH = 64


def clip_code(value: object, width: int = AUDIT_CODE_WIDTH) -> str:
    """把审计用短字段裁到列宽以内，超长时保留尾部省略号。"""
    text = "" if value is None else str(value)
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def pack_ids(ids: list[int], width: int = AUDIT_CODE_WIDTH) -> str:
    """把 ID 列表压成不超过列宽的字符串，如 ``11,9,7…(+24)``。

    批量操作的 ID 列表很容易超过 64 字符，直接拼接会让写审计时事务失败；
    这里优先保留前面的 ID，放不下时用省略号 + 剩余数量收尾。
    """
    values = [str(i) for i in ids]
    joined = ",".join(values)
    if len(joined) <= width:
        return joined

    reserve = len(f"…(+{len(values)})")
    kept: list[str] = []
    for value in values:
        candidate = ",".join(kept + [value])
        if len(candidate) + reserve > width:
            break
        kept.append(value)
    return ",".join(kept) + f"…(+{len(values) - len(kept)})"


def write_audit(db: Session, actor: str, action: str, entity: str, entity_id: str, summary: str, before=None, after=None) -> None:
    from .models import AuditLog

    db.add(
        AuditLog(
            actor=clip_code(actor),
            action=clip_code(action),
            entity=clip_code(entity),
            entity_id=clip_code(entity_id),
            summary=summary,
            before=before,
            after=after,
        )
    )
