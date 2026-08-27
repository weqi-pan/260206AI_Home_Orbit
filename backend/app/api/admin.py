from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.security import get_db, require_admin
from backend.app.db.models import Account, Device, Ticket, User


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/dashboard")
def dashboard(session: Session = Depends(get_db)):
    return {
        "accounts": session.scalar(select(func.count()).select_from(Account)) or 0,
        "users": session.scalar(select(func.count()).select_from(User)) or 0,
        "devices": session.scalar(select(func.count()).select_from(Device)) or 0,
        "open_tickets": session.scalar(select(func.count()).select_from(Ticket).where(Ticket.status == "open")) or 0,
    }
