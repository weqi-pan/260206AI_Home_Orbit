from backend.app.api.home.base import *
from backend.app.api.home.base import _low_stock_models, _parse_datetime, _shopping_view

@router.get("/memos")
def list_memos(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    items = HouseholdMemoService(session).list(membership.household_id)
    return {"items": [{"id": item.id, "content": item.content, "created_at": item.created_at,
                       "updated_at": item.updated_at} for item in items]}


@router.post("/memos", status_code=201)
def create_memo(payload: MemoIn, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    memo = HouseholdMemoService(session).create(membership.household_id, content=payload.content,
                                                account_id=account.id)
    session.commit()
    return {"id": memo.id, "content": memo.content, "created_at": memo.created_at, "updated_at": memo.updated_at}


@router.patch("/memos/{memo_id}")
def patch_memo(memo_id: str, payload: MemoIn, account: Account = Depends(get_current_account),
               session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        memo = HouseholdMemoService(session).update(membership.household_id, memo_id, content=payload.content,
                                                    account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": memo.id, "content": memo.content, "updated_at": memo.updated_at}


@router.delete("/memos/{memo_id}", status_code=204)
def delete_memo(memo_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        HouseholdMemoService(session).remove(membership.household_id, memo_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return None
