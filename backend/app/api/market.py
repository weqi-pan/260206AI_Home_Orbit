# -*- coding: utf-8 -*-
"""Phase 7 marketplace user API (/api/market/*).

Household-scoped discovery (search by name/description, category filter,
official section), entry detail, pinned install with default-deny per-tool
authorization, explicit upgrade with reauthorization, uninstall, favorites,
reports and my-installed listing. Stable 403/404 codes; deprecated entries
hidden from discovery and unsearchable.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, HouseholdMember
from backend.app.market.service import MarketError, MarketInstallService, MarketListingService
from backend.app.schemas.market import MarketInstallRequest, MarketReportRequest, MarketUpgradeRequest

router = APIRouter(prefix="/market", tags=["market"])

INSTALL_ROLES = {"owner", "admin"}


def _household(db, account_id) -> HouseholdMember:
    rows = list(db.scalars(select(HouseholdMember).where(HouseholdMember.account_id == account_id)).all())
    if not rows:
        raise HTTPException(404, detail={"code": "HOUSEHOLD_NOT_FOUND"})
    if len(rows) != 1:
        raise HTTPException(409, detail={"code": "HOUSEHOLD_MEMBERSHIP_AMBIGUOUS"})
    return rows[0]


def _market_error(exc: MarketError):
    return HTTPException(exc.status, detail={"code": exc.code})


@router.get("/listings")
def listings(query: str | None = None, category: str | None = None,
             account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    return MarketListingService(db).listings(member.household_id, query=query, category=category)


@router.get("/entries/{entry_id}")
def entry_detail(entry_id: str, account: Account = Depends(get_current_account),
                 db: Session = Depends(get_db)):
    member = _household(db, account.id)
    try:
        return MarketListingService(db).detail(member.household_id, entry_id)
    except MarketError as exc:
        raise _market_error(exc) from exc


@router.post("/entries/{entry_id}/install", status_code=201)
def install(entry_id: str, payload: MarketInstallRequest, account: Account = Depends(get_current_account),
            db: Session = Depends(get_db)):
    member = _household(db, account.id)
    service = MarketInstallService(db)
    try:
        result = service.install(member.household_id, account.id, entry_id,
                                 payload.granted_tool_keys, role=member.role)
    except MarketError as exc:
        raise _market_error(exc) from exc
    return result


@router.get("/my")
def my_installs(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    return MarketInstallService(db).my_installs(member.household_id)


@router.post("/installations/{installation_id}/upgrade")
def upgrade(installation_id: str, payload: MarketUpgradeRequest,
            account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    service = MarketInstallService(db)
    try:
        return service.upgrade(member.household_id, account.id, installation_id,
                               payload.granted_tool_keys, role=member.role)
    except MarketError as exc:
        raise _market_error(exc) from exc


@router.delete("/installations/{installation_id}", status_code=204)
def uninstall(installation_id: str, revoke_permissions: bool = False,
              account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    service = MarketInstallService(db)
    try:
        service.uninstall(member.household_id, installation_id,
                          revoke_permissions=revoke_permissions, role=member.role)
    except MarketError as exc:
        raise _market_error(exc) from exc
    return None


@router.post("/entries/{entry_id}/favorite", status_code=201)
def favorite(entry_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    try:
        return MarketListingService(db).favorite(member.household_id, entry_id)
    except MarketError as exc:
        raise _market_error(exc) from exc


@router.delete("/entries/{entry_id}/favorite")
def unfavorite(entry_id: str, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    return MarketListingService(db).unfavorite(member.household_id, entry_id)


@router.get("/favorites")
def favorites(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    member = _household(db, account.id)
    return MarketListingService(db).favorites(member.household_id)


@router.post("/entries/{entry_id}/reports", status_code=201)
def report(entry_id: str, payload: MarketReportRequest, account: Account = Depends(get_current_account),
           db: Session = Depends(get_db)):
    member = _household(db, account.id)
    try:
        row = MarketListingService(db).report(member.household_id, entry_id, account.id, payload.reason)
    except MarketError as exc:
        raise _market_error(exc) from exc
    return {"id": row.id, "entry_id": row.entry_id, "status": row.status, "reason": row.reason}
