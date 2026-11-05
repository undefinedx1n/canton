from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, write_audit
from ..models import User
from ..schemas import LoginRequest, TokenResponse, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")
    token = create_access_token(user.username, user.role, user.display_name)
    write_audit(db, user.username, "login", "user", str(user.id), f"{user.display_name} 登录系统")
    db.commit()
    return TokenResponse(access_token=token, display_name=user.display_name, role=user.role)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password")
def change_password(old_password: str, new_password: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.id == user.id).first()
    if not verify_password(old_password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    db_user.password_hash = hash_password(new_password)
    write_audit(db, user.username, "change_password", "user", str(user.id), "修改密码")
    db.commit()
    return {"ok": True}
