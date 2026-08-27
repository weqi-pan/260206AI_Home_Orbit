from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.app.db.models import Account, Household, HouseholdMember, Invitation
from backend.app.schemas.households import HouseholdResponse, InvitationCreate, InvitationResponse, MemberResponse, MembershipResponse


router = APIRouter(prefix="/households", tags=["households"])


def _membership(session: Session, account_id: str, household_id: str | None = None) -> HouseholdMember:
    query = select(HouseholdMember).where(HouseholdMember.account_id == account_id)
    if household_id:
        query = query.where(HouseholdMember.household_id == household_id)
    membership = session.scalar(query)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="家庭不存在")
    return membership


def _response(session: Session, membership: HouseholdMember) -> HouseholdResponse:
    household = session.get(Household, membership.household_id)
    members = session.execute(
        select(HouseholdMember, Account).join(Account, Account.id == HouseholdMember.account_id)
        .where(HouseholdMember.household_id == household.id)
    ).all()
    return HouseholdResponse(
        id=household.id,
        name=household.name,
        role=membership.role,
        members=[MemberResponse(account_id=item.account_id, username=account.username, role=item.role) for item, account in members],
    )


@router.get("/current", response_model=HouseholdResponse)
def current_household(account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    return _response(session, _membership(session, account.id))


@router.get("/{household_id}", response_model=HouseholdResponse)
def get_household(household_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    return _response(session, _membership(session, account.id, household_id))


@router.post("/{household_id}/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
def create_invitation(payload: InvitationCreate, household_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    membership = _membership(session, account.id, household_id)
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要家庭所有者或管理员权限")
    invited = session.scalar(select(Account).where(Account.username == payload.username, Account.status == "active"))
    if not invited:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="受邀账号不存在")
    if invited.id == account.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不能邀请自己")
    invitation = Invitation(
        household_id=household_id,
        invited_account_id=invited.id,
        invited_by_account_id=account.id,
        role=payload.role,
    )
    session.add(invitation)
    session.commit()
    session.refresh(invitation)
    return invitation


@router.post("/invitations/{invitation_id}/accept", response_model=MembershipResponse)
def accept_invitation(invitation_id: str, account: Account = Depends(get_current_account), session: Session = Depends(get_db)):
    invitation = session.scalar(select(Invitation).where(
        Invitation.id == invitation_id,
        Invitation.invited_account_id == account.id,
        Invitation.status == "pending",
    ))
    if not invitation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="邀请不存在")
    existing = _membership(session, account.id)
    if existing.role == "owner":
        other_members = session.scalar(select(HouseholdMember).where(
            HouseholdMember.household_id == existing.household_id,
            HouseholdMember.account_id != account.id,
        ))
        if other_members:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="请先转移原家庭所有权")
    old_household = session.get(Household, existing.household_id)
    session.delete(existing)
    session.flush()
    if old_household and not session.scalar(select(HouseholdMember).where(HouseholdMember.household_id == old_household.id)):
        session.delete(old_household)
    new_membership = HouseholdMember(household_id=invitation.household_id, account_id=account.id, role=invitation.role)
    invitation.status = "accepted"
    invitation.accepted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(new_membership)
    session.commit()
    return MembershipResponse(household_id=new_membership.household_id, role=new_membership.role)
