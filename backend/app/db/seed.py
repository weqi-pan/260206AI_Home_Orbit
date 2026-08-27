import csv
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.core.security import hash_password
from backend.app.db.models import Account, Device, DeviceUsageRecord, Household, HouseholdMember, Inventory, KnowledgeFile, User


DEVICES = [
    ("R1-Lite", "清巧扫地机器人 R1-Lite", "扫地机器人", "吸力2800Pa; 续航100分钟", "轻量入门款", 1299.0, "自动回充; 定时清扫; 防跌落传感器"),
    ("R1-Pro", "全能扫拖机器人 R1-Pro", "扫拖一体机器人", "吸力4500Pa; 续航150分钟", "均衡型扫拖一体机", 2499.0, "扫拖一体; 激光导航; 地毯增压"),
    ("RT2-Max", "旗舰自清洁扫拖机器人 RT2-Max", "扫拖一体机器人", "吸力6000Pa; 续航200分钟", "旗舰款", 3999.0, "自动集尘; 自动洗拖布; AI避障"),
]

INVENTORY = [
    ("R1-Lite", "北京仓", 36), ("R1-Lite", "上海仓", 24),
    ("R1-Pro", "北京仓", 18), ("R1-Pro", "上海仓", 32), ("R1-Pro", "广州仓", 12),
    ("RT2-Max", "上海仓", 9), ("RT2-Max", "广州仓", 7),
]


def _seed_accounts(session: Session, settings: Settings) -> None:
    user = session.get(User, "1001")
    if not user:
        user = User(user_id="1001", username="演示用户", city="上海")
        session.add(user)
        session.flush()
    admin = session.scalar(select(Account).where(Account.username == "admin"))
    if not admin:
        admin = Account(
            username="admin",
            password_hash=hash_password(settings.admin_initial_password, settings.bcrypt_rounds),
            role="admin",
            must_change_password=True,
        )
        session.add(admin)
        session.flush()
    demo = session.scalar(select(Account).where(Account.username == "demo"))
    if not demo:
        demo = Account(
            username="demo",
            password_hash=hash_password(settings.demo_initial_password, settings.bcrypt_rounds),
            role="user",
            user_id="1001",
        )
        session.add(demo)
        session.flush()
    for account in (admin, demo):
        if session.scalar(select(HouseholdMember).where(HouseholdMember.account_id == account.id)):
            continue
        household = Household(name=f"{account.username}的家庭", created_by_account_id=account.id)
        session.add(household)
        session.flush()
        session.add(HouseholdMember(household_id=household.id, account_id=account.id, role="owner"))


def _seed_devices(session: Session) -> None:
    for row in DEVICES:
        device = session.get(Device, row[0])
        values = dict(zip(("model_id", "model_name", "category", "specs", "description", "price", "features"), row))
        if not device:
            session.add(Device(**values))
    session.flush()
    for model_id, warehouse, stock in INVENTORY:
        item = session.scalar(select(Inventory).where(
            Inventory.model_id == model_id, Inventory.warehouse == warehouse
        ))
        if not item:
            session.add(Inventory(model_id=model_id, warehouse=warehouse, stock=stock))


def _seed_usage_csv(session: Session, csv_path: Path) -> None:
    if not csv_path.exists():
        return
    with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            user_id = row["用户ID"].strip()
            user = session.get(User, user_id)
            if not user:
                session.add(User(user_id=user_id, username=f"演示用户{user_id}", city="上海"))
                session.flush()
            record = session.scalar(select(DeviceUsageRecord).where(
                DeviceUsageRecord.user_id == user_id,
                DeviceUsageRecord.month == row["时间"].strip(),
            ))
            values = {
                "feature": row["特征"].strip(),
                "efficiency": row["清洁效率"].strip(),
                "consumables": row["耗材"].strip(),
                "comparison": row["对比"].strip(),
            }
            if not record:
                session.add(DeviceUsageRecord(user_id=user_id, month=row["时间"].strip(), **values))


def _seed_knowledge(session: Session, knowledge_path: Path) -> None:
    if not knowledge_path.exists():
        return
    for path in knowledge_path.iterdir():
        if path.is_file() and path.suffix.lower() in {".txt", ".pdf"}:
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            if not session.scalar(select(KnowledgeFile).where(KnowledgeFile.file_md5 == digest)):
                session.add(KnowledgeFile(filename=path.name, file_md5=digest, file_type=path.suffix.lower()))


def seed_database(session_factory: sessionmaker[Session], settings: Settings) -> None:
    with session_factory.begin() as session:
        _seed_accounts(session, settings)
        _seed_devices(session)
        _seed_usage_csv(session, Path(settings.usage_csv_path))
        _seed_knowledge(session, Path(settings.knowledge_path))
