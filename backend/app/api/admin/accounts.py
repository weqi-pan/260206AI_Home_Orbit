from backend.app.api.admin.base import *
from backend.app.api.admin.base import account_dict, _audit
from backend.app.services import auth_sessions

@router.get("/accounts")
def accounts(_admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    return [account_dict(item) for item in db.scalars(select(Account).order_by(Account.created_at)).all()]


@router.put("/accounts/{account_id}")
def update_account(account_id: str, payload: AccountUpdate, admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(Account, account_id)
    if not item: raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账号不存在"})
    disabling_admin = item.role == "admin" and item.status == "active" and (
        payload.status == "disabled" or payload.role == "user"
    )
    if item.id == admin.id and disabling_admin:
        raise HTTPException(status_code=400,
                            detail={"code": "ADMIN_SELF_LOCKOUT", "message": "不能禁用或降级当前管理员账号"})
    if disabling_admin:
        active_admins = db.scalar(select(func.count()).select_from(Account).where(
            Account.role == "admin", Account.status == "active"
        )) or 0
        if active_admins <= 1:
            raise HTTPException(status_code=400,
                                detail={"code": "LAST_ADMIN_PROTECTED", "message": "至少保留一个启用的管理员账号"})
    previous = {"status": item.status, "role": item.role}
    if payload.status is not None: item.status = payload.status
    if payload.role is not None: item.role = payload.role
    # 禁用账号要立刻断掉它手上的令牌。只在「active → disabled」这一次跃迁上做，
    # 否则每次对已禁用账号做无状态变更都会再 +1，审计里看不出到底发生过什么。
    just_disabled = previous["status"] == "active" and item.status == "disabled"
    if just_disabled:
        auth_sessions.revoke_all_for_account(db, item.id)
        auth_sessions.bump_token_version(db, item)
    db.commit(); db.refresh(item)
    _audit(db, admin, "admin.account.update", "account", item.id,
           {"previous": previous, "status": item.status, "role": item.role})
    return account_dict(item)


@router.post("/accounts/{account_id}/reset-password", status_code=204)
def reset_password(account_id: str, payload: ResetPassword, request: Request, response: Response,
                   admin: Account = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(Account, account_id)
    if not item: raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账号不存在"})
    item.password_hash = hash_password(payload.password, request.app.state.settings.bcrypt_rounds)
    item.must_change_password = True
    # 重置密码的语义就是「把这个人请出所有设备」：旧令牌与会话必须一起作废
    auth_sessions.revoke_all_for_account(db, item.id)
    auth_sessions.bump_token_version(db, item)
    db.commit(); response.status_code = 204
    _audit(db, admin, "admin.account.reset_password", "account", item.id, {"must_change_password": True})
