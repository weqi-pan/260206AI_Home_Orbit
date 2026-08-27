from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.api.households import _membership
from backend.app.api.setup import setup_status
from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, HouseholdMember


router = APIRouter(prefix="/home", tags=["home"])


class OrbitNode(BaseModel):
    id: str
    label: str
    kind: str
    status: str
    href: str
    detail: str


class OrbitResponse(BaseModel):
    nodes: list[OrbitNode]


@router.get("/orbit", response_model=OrbitResponse)
def orbit(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id)
    member_count = session.scalar(select(func.count()).select_from(HouseholdMember).where(
        HouseholdMember.household_id == membership.household_id
    ))
    setup = setup_status(session, account)
    nodes = []
    if not setup.ready:
        nodes.append(OrbitNode(
            id="setup", label="完成模型初始化", kind="setup", status="attention", href="/admin/setup",
            detail="主聊天与向量模型尚未配置",
        ))
    nodes.append(OrbitNode(
        id="household", label="家庭空间", kind="household", status="ready", href="/household",
        detail=f"{member_count} 位家庭成员",
    ))
    return OrbitResponse(nodes=nodes)
