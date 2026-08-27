from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from backend.app.db.models import Device, DeviceUsageRecord, FaqPair, Inventory, RestockNotification, Ticket


@dataclass(frozen=True)
class ToolRepositories:
    """Narrow database gateway exposed to built-ins instead of a session factory."""

    _sessions: object

    def search_devices(self, keyword):
        with self._sessions() as db:
            pattern = f"%{keyword.strip()}%"
            return list(db.scalars(select(Device).where(or_(Device.model_id.like(pattern), Device.model_name.like(pattern),
                                                              Device.features.like(pattern)))).all())

    def device(self, model_id):
        with self._sessions() as db:
            return db.get(Device, model_id)

    def inventory(self, model_id):
        with self._sessions() as db:
            return list(db.scalars(select(Inventory).where(Inventory.model_id == model_id)).all())

    def devices(self, model_ids):
        with self._sessions() as db:
            return list(db.scalars(select(Device).where(Device.model_id.in_(model_ids))).all())

    def approved_faq(self, query):
        with self._sessions() as db:
            pattern = f"%{query}%"
            return list(db.scalars(select(FaqPair).where(FaqPair.status == "approved",
                or_(FaqPair.question.like(pattern), FaqPair.answer.like(pattern)))).all())

    def usage(self, user_id, month):
        with self._sessions() as db:
            return db.scalar(select(DeviceUsageRecord).where(DeviceUsageRecord.user_id == user_id,
                                                               DeviceUsageRecord.month == month))

    def register_restock(self, account_id, model_id):
        with self._sessions() as db:
            item = db.scalar(select(RestockNotification).where(RestockNotification.account_id == account_id,
                                                                 RestockNotification.model_id == model_id))
            if item is None:
                item = RestockNotification(account_id=account_id, model_id=model_id); db.add(item)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    item = db.scalar(select(RestockNotification).where(RestockNotification.account_id == account_id,
                                                                         RestockNotification.model_id == model_id))
                    if item is None:
                        raise
                else:
                    db.refresh(item)
            return item

    def create_ticket(self, account_id, session_id, title, content):
        with self._sessions.begin() as db:
            item = db.scalar(select(Ticket).where(Ticket.account_id == account_id, Ticket.session_id == session_id,
                                                   Ticket.status == "open", Ticket.title == title))
            if item is None:
                item = Ticket(account_id=account_id, session_id=session_id, title=title, summary=content[:200])
                db.add(item); db.flush()
            return item


@dataclass(frozen=True)
class ToolCapabilities:
    search_devices: object
    device: object
    inventory: object
    devices: object
    approved_faq: object
    usage: object
    register_restock: object
    create_ticket: object

    @classmethod
    def bind(cls, repositories):
        return cls(*(getattr(repositories, name) for name in (
            "search_devices", "device", "inventory", "devices", "approved_faq", "usage",
            "register_restock", "create_ticket")))
