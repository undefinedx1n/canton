from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, write_audit
from ..models import User
from ..schemas import SettingIn
from ..services.config_service import get_all_settings, set_setting

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_settings(db: Session = Depends(get_db)):
    return get_all_settings(db)


@router.put("/{key}")
def update_setting(key: str, payload: SettingIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = set_setting(db, key, payload.value, payload.description)
    write_audit(
        db, user.username, "update", "setting", key,
        f"修改系统配置 {key}",
        after={"value": payload.value},
    )
    db.commit()
    return {"key": row.key, "value": row.value, "description": row.description}
