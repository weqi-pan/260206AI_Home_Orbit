from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from backend.app.db.models import Device, DeviceUsageRecord, FaqPair, Inventory, RestockNotification, Ticket


@dataclass(frozen=True)
class ToolRepositories:
    """Narrow database gateway exposed to built-ins instead of a session factory."""

    _sessions: object

    def search_devices(self, household_id, keyword):
        with self._sessions() as db:
            pattern = f"%{keyword.strip()}%"
            return list(db.scalars(select(Device).where(Device.household_id == household_id,
                or_(Device.model_id.like(pattern), Device.model_name.like(pattern),
                    Device.category.like(pattern), Device.description.like(pattern),
                    Device.features.like(pattern))).order_by(Device.price)).all())

    def device(self, household_id, model_id):
        with self._sessions() as db:
            return db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == model_id))

    def inventory(self, household_id, model_id):
        with self._sessions() as db:
            return list(db.scalars(select(Inventory).where(Inventory.household_id == household_id,
                                                            Inventory.model_id == model_id)).all())

    def total_stock(self, household_id, model_ids):
        with self._sessions() as db:
            return dict(db.execute(select(Inventory.model_id, func.sum(Inventory.stock)).where(
                Inventory.household_id == household_id,
                Inventory.model_id.in_(model_ids)).group_by(Inventory.model_id)).all())

    def devices(self, household_id, model_ids):
        with self._sessions() as db:
            return list(db.scalars(select(Device).where(Device.household_id == household_id,
                                                         Device.model_id.in_(model_ids))).all())

    def approved_faq(self, household_id, query):
        with self._sessions() as db:
            pattern = f"%{query}%"
            return list(db.scalars(select(FaqPair).where(FaqPair.household_id == household_id, FaqPair.status == "approved",
                or_(FaqPair.question.like(pattern), FaqPair.answer.like(pattern)))).all())

    def usage(self, household_id, user_id, month):
        with self._sessions() as db:
            return db.scalar(select(DeviceUsageRecord).where(DeviceUsageRecord.household_id == household_id,
                                                                DeviceUsageRecord.user_id == user_id,
                                                                DeviceUsageRecord.month == month))

    def restock_notification(self, household_id, account_id, model_id):
        with self._sessions() as db:
            return db.scalar(select(RestockNotification).where(RestockNotification.household_id == household_id,
                RestockNotification.account_id == account_id, RestockNotification.model_id == model_id))

    def register_restock(self, household_id, account_id, model_id):
        with self._sessions() as db:
            device = db.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == model_id))
            if not device:
                raise LookupError("DEVICE_NOT_FOUND")
            item = db.scalar(select(RestockNotification).where(RestockNotification.household_id == household_id,
                                                                 RestockNotification.account_id == account_id,
                                                                 RestockNotification.model_id == model_id))
            if item is None:
                item = RestockNotification(household_id=household_id, account_id=account_id,
                                           device_id=device.id, model_id=model_id); db.add(item)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    item = db.scalar(select(RestockNotification).where(RestockNotification.household_id == household_id,
                                                                         RestockNotification.account_id == account_id,
                                                                         RestockNotification.model_id == model_id))
                    if item is None:
                        raise
                else:
                    db.refresh(item)
            return item

    def create_ticket(self, household_id, account_id, session_id, title, content,
                      summary=None, emotion=None, priority=None):
        with self._sessions.begin() as db:
            item = db.scalar(select(Ticket).where(Ticket.household_id == household_id,
                                                   Ticket.account_id == account_id, Ticket.session_id == session_id,
                                                   Ticket.status == "open", Ticket.title == title))
            if item is None:
                item = Ticket(household_id=household_id, account_id=account_id, session_id=session_id,
                              title=title, summary=summary if summary is not None else content[:200],
                              emotion=emotion, priority=priority or "normal")
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
