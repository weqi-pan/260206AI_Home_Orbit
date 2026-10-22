from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import get_current_account, get_db
from backend.agent.tools.gateways import ToolRepositories
from backend.app.db.models import Account, ChatSession, HouseholdMember, Ticket
from backend.app.llm_runtime.execution import purpose_model_resolver
from backend.app.services.ticket_summary import TicketSummaryService


router = APIRouter(prefix="/tickets", tags=["tickets"])


class TicketCreate(BaseModel):
    session_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    emotion: str | None = None
    priority: str = Field(default="normal", pattern="^(low|normal|high)$")


def ticket_dict(item):
    return {"id": item.id, "session_id": item.session_id, "title": item.title, "summary": item.summary, "emotion": item.emotion,
            "priority": item.priority, "status": item.status, "admin_reply": item.admin_reply, "created_at": item.created_at,
            "resolved_at": item.resolved_at}


@router.get("/my")
def my_tickets(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
    return [ticket_dict(item) for item in db.scalars(select(Ticket).where(
        Ticket.household_id == household_id, Ticket.account_id == account.id).order_by(Ticket.id.desc())).all()]


@router.post("", status_code=201)
def create(payload: TicketCreate, request: Request, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
    if payload.session_id and not db.scalar(select(ChatSession).where(ChatSession.id == payload.session_id,
                                                                       ChatSession.household_id == household_id,
                                                                       ChatSession.account_id == account.id)):
        raise HTTPException(status_code=404, detail="会话不存在")
    existing = db.scalar(select(Ticket).where(
        Ticket.household_id == household_id, Ticket.account_id == account.id,
        Ticket.session_id == payload.session_id,
        Ticket.status == "open",
        Ticket.title == payload.title,
    ))
    if existing:
        return ticket_dict(existing)
    try:
        service = getattr(request.app.state, "ticket_summary_service", None)
        if service is None:
            service = TicketSummaryService(model_resolver=purpose_model_resolver(request.app.state.database.session_factory, household_id))
        summary = service.summarize(payload.content)
    except Exception:
        summary = payload.content[:200]
    item = ToolRepositories(request.app.state.database.session_factory).create_ticket(
        household_id, account.id, payload.session_id, payload.title, payload.content,
        summary=summary[:1000], emotion=payload.emotion, priority=payload.priority)
    return ticket_dict(item)


@router.get("/my/{ticket_id}")
def detail(ticket_id: int, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    household_id = db.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == account.id))
    item = db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.household_id == household_id,
                                          Ticket.account_id == account.id))
    if not item:
        raise HTTPException(status_code=404, detail="工单不存在")
    return ticket_dict(item)
