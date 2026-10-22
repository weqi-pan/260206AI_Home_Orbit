from backend.app.api.home.base import *
from backend.app.api.home.base import _low_stock_models, _parse_datetime, _shopping_view
from backend.app.api.households import _membership

@router.get("/shopping-list")
def list_shopping(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    service = ShoppingListService(session)
    return {"items": [_shopping_view(item) for item in service.list(membership.household_id)],
            "summary": service.summary(membership.household_id)}


@router.post("/shopping-list", status_code=201)
def create_shopping(payload: ShoppingItemIn, account: Account = Depends(get_current_account),
                    session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    item = ShoppingListService(session).upsert(membership.household_id, name=payload.name,
                                               quantity=payload.quantity, unit=payload.unit,
                                               category=payload.category, note=payload.note, account_id=account.id)
    session.commit()
    return _shopping_view(item)


@router.patch("/shopping-list/{item_id}")
def patch_shopping(item_id: str, payload: ShoppingItemPatch, account: Account = Depends(get_current_account),
                   session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    service = ShoppingListService(session)
    try:
        item = service.modify(membership.household_id, item_id, name=payload.name, quantity=payload.quantity,
                              unit=payload.unit, category=payload.category, note=payload.note, account_id=account.id)
        if payload.checked is not None:
            item = service.set_checked(membership.household_id, item_id, payload.checked, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return _shopping_view(item)


@router.delete("/shopping-list/{item_id}", status_code=204)
def delete_shopping(item_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        ShoppingListService(session).remove(membership.household_id, item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return None
