from backend.app.api.home.base import *
from backend.app.api.home.base import _low_stock_models, _parse_datetime, _shopping_view

@router.get("/preferences")
def list_preferences(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    items = PreferenceService(session).list(membership.household_id)
    return {"items": [{"key": item.key, "value": item.value, "category": item.category,
                       "value_type": item.value_type, "updated_at": item.updated_at} for item in items]}


@router.put("/preferences")
def set_preference(payload: PreferenceIn, account: Account = Depends(get_current_account),
                   session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        row = PreferenceService(session).set_confirmed(membership.household_id, key=payload.key, value=payload.value,
                                                       value_type=payload.value_type, category=payload.category,
                                                       account_id=account.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    session.commit()
    return {"key": row.key, "value": row.value, "category": row.category, "value_type": row.value_type}


@router.get("/preferences/proposals")
def list_proposals(status: str = "pending", account: Account = Depends(get_current_account),
                   session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    items = PreferenceService(session).proposals(membership.household_id, status=status)
    return {"items": [{"id": item.id, "key": item.key, "value": item.value, "category": item.category,
                       "value_type": item.value_type, "reason": item.reason, "status": item.status,
                       "proposed_by_kind": item.proposed_by_kind} for item in items]}


@router.post("/preferences/proposals", status_code=201)
def create_proposal(payload: ProposalIn, account: Account = Depends(get_current_account),
                    session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    service = PreferenceService(session)
    try:
        proposal = service.propose(membership.household_id, key=payload.key, value=payload.value,
                                   value_type=payload.value_type, category=payload.category,
                                   reason=payload.reason, proposed_by_account_id=account.id,
                                   proposed_by_kind="member")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    session.commit()
    return {"id": proposal.id, "key": proposal.key, "value": proposal.value, "status": proposal.status}


@router.post("/preferences/proposals/{proposal_id}/confirm")
def confirm_proposal(proposal_id: str, account: Account = Depends(get_current_account),
                     session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        row = PreferenceService(session).confirm(membership.household_id, proposal_id, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"key": row.key, "value": row.value, "category": row.category, "value_type": row.value_type}


@router.post("/preferences/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, account: Account = Depends(get_current_account),
                    session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    try:
        proposal = PreferenceService(session).reject(membership.household_id, proposal_id, account_id=account.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.args[0]}) from exc
    session.commit()
    return {"id": proposal.id, "status": proposal.status}
