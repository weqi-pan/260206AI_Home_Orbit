import csv
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import Settings
from backend.app.core.security import hash_password
from backend.agent.platform.catalog import BuiltinToolRegistry
from backend.agent.platform.versions import normalize_json, validate_agent_definition, validate_skill_definition
from backend.app.db.base import utcnow
from backend.app.db.models import (Account, AgentDefinition, AgentDeployment, AgentInstallation, AgentSkillBinding,
                                   AgentToolBinding, AgentVersion, Device, DeviceUsageRecord, Household, HouseholdMember,
                                   FaqPair, InstallationPermission, Inventory, KnowledgeFile, Skill, SkillVersion, ToolDefinition,
                                   ToolPermission, User)


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


def _seed_devices(session: Session, household_id: str) -> None:
    for row in DEVICES:
        device = session.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == row[0]))
        values = dict(zip(("model_id", "model_name", "category", "specs", "description", "price", "features"), row))
        if not device:
            session.add(Device(household_id=household_id, **values))
    session.flush()
    for model_id, warehouse, stock in INVENTORY:
        device = session.scalar(select(Device).where(Device.household_id == household_id, Device.model_id == model_id))
        item = session.scalar(select(Inventory).where(
            Inventory.household_id == household_id, Inventory.model_id == model_id, Inventory.warehouse == warehouse
        ))
        if not item:
            session.add(Inventory(household_id=household_id, device_id=device.id,
                                  model_id=model_id, warehouse=warehouse, stock=stock))


def _seed_usage_csv(session: Session, csv_path: Path, household_id: str) -> None:
    if not csv_path.exists():
        return
    with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            user_id = row["用户ID"].strip()
            user = session.get(User, user_id)
            if not user:
                session.add(User(user_id=user_id, username=f"演示用户{user_id}", city="上海"))
                session.flush()
            record = session.scalar(select(DeviceUsageRecord).where(DeviceUsageRecord.household_id == household_id,
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
                session.add(DeviceUsageRecord(household_id=household_id, user_id=user_id,
                                              month=row["时间"].strip(), **values))


def _seed_knowledge(session: Session, knowledge_path: Path, household_id: str) -> None:
    if not knowledge_path.exists():
        return
    for path in knowledge_path.iterdir():
        if path.is_file() and path.suffix.lower() in {".txt", ".pdf"}:
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            if not session.scalar(select(KnowledgeFile).where(KnowledgeFile.household_id == household_id,
                                                               KnowledgeFile.file_md5 == digest)):
                session.add(KnowledgeFile(household_id=household_id, filename=path.name,
                                          file_md5=digest, file_type=path.suffix.lower()))
    if not session.scalar(select(FaqPair).where(FaqPair.household_id == household_id,
                                                 FaqPair.question == "设备保修期是多久？")):
        session.add(FaqPair(household_id=household_id, question="设备保修期是多久？",
                            answer="整机保修一年，主要部件按产品说明执行。", status="approved", reviewed_at=utcnow()))


DEVICE_TOOL_KEYS = (
    "device.search", "device.detail", "device.inventory", "device.compare", "device.knowledge.search",
    "device.usage.report", "device.restock.register", "device.ticket.create", "device.faq.search",
)
DEVICE_SKILLS = {
    "设备诊断": "诊断设备问题，优先检索设备详情、家庭知识与已审核FAQ。",
    "设备选购": "根据需求搜索设备、读取库存并用 Markdown 表格比较候选型号。",
    "库存与使用报告": "查询库存、低库存状态和当前家庭成员的月度设备使用记录。",
    "售后升级": "必要时登记到货通知或创建售后工单，写操作必须经过安装授权。",
}


def _seed_catalog(session):
    registry = BuiltinToolRegistry.default()
    for metadata in registry.metadata():
        row = session.scalar(select(ToolDefinition).where(ToolDefinition.key == metadata.key))
        if row is None:
            row = ToolDefinition(key=metadata.key, display_name=metadata.display_name,
                                 description=metadata.description, risk_level=metadata.risk_level,
                                 version=metadata.version, enabled=True)
            session.add(row)
        elif (row.display_name, row.description, row.risk_level, row.version, row.enabled) != (
                metadata.display_name, metadata.description, metadata.risk_level, metadata.version, True):
            raise RuntimeError("TOOL_CATALOG_VERSION_CONFLICT")
    session.flush()


def seed_official_device_agent(session: Session, household_id: str, owner_id: str):
    _seed_catalog(session)
    skills = []
    for name, instructions in DEVICE_SKILLS.items():
        skill = session.scalar(select(Skill).where(Skill.household_id == household_id, Skill.name == name))
        if skill is None:
            skill = Skill(household_id=household_id, name=name); session.add(skill); session.flush()
        version = session.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == 1))
        definition = normalize_json(validate_skill_definition({"instructions": instructions}))
        if version is None:
            version = SkillVersion(household_id=household_id, skill_id=skill.id, version=1,
                                   definition_json=definition, published_at=utcnow())
            session.add(version); session.flush()
        elif version.definition_json != definition:
            raise RuntimeError("OFFICIAL_SKILL_VERSION_IMMUTABLE")
        skills.append(version)
    agent = session.scalar(select(AgentDefinition).where(
        AgentDefinition.household_id == household_id, AgentDefinition.name == "设备管家"))
    if agent is None:
        agent = AgentDefinition(household_id=household_id, name="设备管家"); session.add(agent); session.flush()
    definition = validate_agent_definition({
        "prompt": "你是官方设备管家。仅使用已安装授权的设备工具，并始终限制在当前家庭。",
        "tools": list(DEVICE_TOOL_KEYS),
        "permissions": [{"tool_key": key, "required": True} for key in DEVICE_TOOL_KEYS],
        "budgets": {"tool_calls": 16, "tokens": 32000, "wall_time_seconds": 120, "delegation_depth": 0},
        "metadata": {"description": "官方设备、库存、知识、报告与售后助手"},
    })
    normalized = normalize_json(definition)
    version = session.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == 1))
    if version is None:
        version = AgentVersion(household_id=household_id, agent_id=agent.id, version=1,
                               definition_json=normalized, published_at=utcnow())
        session.add(version); session.flush()
    elif version.definition_json != normalized:
        raise RuntimeError("OFFICIAL_AGENT_VERSION_IMMUTABLE")
    for skill_version in skills:
        if not session.scalar(select(AgentSkillBinding).where(AgentSkillBinding.agent_version_id == version.id,
                                                               AgentSkillBinding.skill_version_id == skill_version.id)):
            session.add(AgentSkillBinding(household_id=household_id, agent_version_id=version.id,
                                          skill_version_id=skill_version.id))
    tools = list(session.scalars(select(ToolDefinition).where(ToolDefinition.key.in_(DEVICE_TOOL_KEYS))).all())
    for tool in tools:
        if not session.scalar(select(AgentToolBinding).where(AgentToolBinding.agent_version_id == version.id,
                                                              AgentToolBinding.tool_id == tool.id)):
            session.add(AgentToolBinding(household_id=household_id, agent_version_id=version.id, tool_id=tool.id))
        if tool.risk_level == "low" and not session.scalar(select(ToolPermission).where(
                ToolPermission.household_id == household_id, ToolPermission.tool_id == tool.id)):
            session.add(ToolPermission(household_id=household_id, tool_id=tool.id,
                                       tool_version=tool.version, granted_by_account_id=owner_id))
    deployment = session.scalar(select(AgentDeployment).where(AgentDeployment.household_id == household_id,
                                                               AgentDeployment.agent_version_id == version.id))
    if deployment is None:
        deployment = AgentDeployment(household_id=household_id, agent_version_id=version.id,
                                     revision=1, visibility="official")
        session.add(deployment); session.flush()
    installation = session.scalar(select(AgentInstallation).where(AgentInstallation.household_id == household_id,
        AgentInstallation.deployment_id == deployment.id, AgentInstallation.agent_version_id == version.id))
    if installation is None:
        installation = AgentInstallation(household_id=household_id, deployment_id=deployment.id,
                                         agent_version_id=version.id, status="installed", authority_slot="active")
        session.add(installation); session.flush()
    for tool in tools:
        if tool.risk_level != "low":
            continue
        if not session.scalar(select(InstallationPermission).where(
                InstallationPermission.installation_id == installation.id, InstallationPermission.tool_id == tool.id)):
            session.add(InstallationPermission(household_id=household_id, installation_id=installation.id,
                tool_id=tool.id, tool_version=tool.version, granted_by_account_id=owner_id))
    return deployment


def seed_database(session_factory: sessionmaker[Session], settings: Settings) -> None:
    with session_factory.begin() as session:
        _seed_accounts(session, settings)
        households = session.execute(select(HouseholdMember.household_id, HouseholdMember.account_id)).all()
        demo = session.scalar(select(Account).where(Account.username == "demo"))
        demo_household_id = session.scalar(select(HouseholdMember.household_id).where(HouseholdMember.account_id == demo.id))
        for household_id, owner_id in households:
            _seed_devices(session, household_id)
            _seed_knowledge(session, Path(settings.knowledge_path), household_id)
            device_deployment = seed_official_device_agent(session, household_id, owner_id)
            seed_official_household_agents(session, household_id, owner_id, device_deployment)
        _seed_usage_csv(session, Path(settings.usage_csv_path), demo_household_id)


CHEF_TOOL_KEYS = ("household.recipe.scale", "household.shopping.read", "household.shopping.write",
                  "household.knowledge.search")
PLANNER_TOOL_KEYS = ("household.task.create", "household.task.update", "household.task.confirm",
                     "household.reminder.create", "household.reminder.cancel", "household.memo.write",
                     "household.preference.read", "household.preference.propose", "household.notification.read")
ENCYCLOPEDIA_TOOL_KEYS = ("household.knowledge.search",)

HOUSEHOLD_SKILLS = {
    "家庭厨师": {
        "菜谱知识": "根据家庭成员问题检索菜谱知识，回答家常菜做法。",
        "按人数换算": "使用菜谱用量换算工具按用餐人数确定性换算食材用量。",
        "采购清单": "根据菜谱用量生成购物清单条目并写入家庭购物清单。",
    },
    "家庭计划员": {
        "家务计划": "创建和更新家庭任务，明确标题、描述与时间。",
        "周期提醒": "为家庭任务创建周期提醒，并在用户要求时取消。",
        "任务确认": "根据家庭成员反馈确认完成或重新打开任务。",
    },
    "生活百科": {
        "家庭常识检索": "只在当前家庭的隔离向量知识中检索生活常识，不做任何兜底猜测。",
    },
}

HOUSEHOLD_PROMPTS = {
    "家庭厨师": "你是官方家庭厨师。只使用已安装授权的家庭工具，按人数换算食材并维护购物清单，始终限制在当前家庭。",
    "家庭计划员": "你是官方家庭计划员。只使用已安装授权的家庭工具，制定家务计划、创建周期提醒、维护家庭备忘；结构化偏好只能提出建议，等待家庭成员确认。",
    "生活百科": "你是官方生活百科。只使用家庭知识检索工具回答生活常识问题，检索范围为当前家庭的隔离知识。",
    "家庭总管": "你是官方家庭总管。根据家庭成员请求委派已安装的官方子代理（设备管家、家庭厨师、家庭计划员、生活百科）完成任务，不直接执行业务工具。",
}


def _seed_official_agent(session, household_id, owner_id, name, tool_keys, skills, prompt, children=(),
                         delegation_depth=0):
    from backend.app.db.models import AgentSkillBinding, AgentToolBinding
    skill_versions = []
    for skill_name, instructions in skills.items():
        skill = session.scalar(select(Skill).where(Skill.household_id == household_id, Skill.name == skill_name))
        if skill is None:
            skill = Skill(household_id=household_id, name=skill_name); session.add(skill); session.flush()
        version = session.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == 1))
        definition = normalize_json(validate_skill_definition({"instructions": instructions}))
        if version is None:
            version = SkillVersion(household_id=household_id, skill_id=skill.id, version=1,
                                   definition_json=definition, published_at=utcnow())
            session.add(version); session.flush()
        elif version.definition_json != definition:
            raise RuntimeError("OFFICIAL_SKILL_VERSION_IMMUTABLE")
        skill_versions.append(version)
    agent = session.scalar(select(AgentDefinition).where(
        AgentDefinition.household_id == household_id, AgentDefinition.name == name))
    if agent is None:
        agent = AgentDefinition(household_id=household_id, name=name); session.add(agent); session.flush()
    definition = validate_agent_definition({
        "prompt": prompt,
        "tools": list(tool_keys),
        "permissions": [{"tool_key": key, "required": True} for key in tool_keys],
        "budgets": {"tool_calls": 16, "tokens": 32000, "wall_time_seconds": 120,
                    "delegation_depth": delegation_depth},
        "metadata": {"description": f"官方{name}"},
        **({"child_deployments": list(children)} if children else {}),
    })
    normalized = normalize_json(definition)
    version = session.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == 1))
    if version is None:
        version = AgentVersion(household_id=household_id, agent_id=agent.id, version=1,
                               definition_json=normalized, published_at=utcnow())
        session.add(version); session.flush()
    elif version.definition_json != normalized:
        raise RuntimeError("OFFICIAL_AGENT_VERSION_IMMUTABLE")
    for skill_version in skill_versions:
        if not session.scalar(select(AgentSkillBinding).where(AgentSkillBinding.agent_version_id == version.id,
                                                               AgentSkillBinding.skill_version_id == skill_version.id)):
            session.add(AgentSkillBinding(household_id=household_id, agent_version_id=version.id,
                                          skill_version_id=skill_version.id))
    tools = list(session.scalars(select(ToolDefinition).where(ToolDefinition.key.in_(tool_keys))).all())
    for tool in tools:
        if not session.scalar(select(AgentToolBinding).where(AgentToolBinding.agent_version_id == version.id,
                                                              AgentToolBinding.tool_id == tool.id)):
            session.add(AgentToolBinding(household_id=household_id, agent_version_id=version.id, tool_id=tool.id))
        if tool.risk_level == "low" and not session.scalar(select(ToolPermission).where(
                ToolPermission.household_id == household_id, ToolPermission.tool_id == tool.id)):
            session.add(ToolPermission(household_id=household_id, tool_id=tool.id,
                                       tool_version=tool.version, granted_by_account_id=owner_id))
    deployment = session.scalar(select(AgentDeployment).where(AgentDeployment.household_id == household_id,
                                                               AgentDeployment.agent_version_id == version.id))
    if deployment is None:
        deployment = AgentDeployment(household_id=household_id, agent_version_id=version.id,
                                     revision=1, visibility="official")
        session.add(deployment); session.flush()
    installation = session.scalar(select(AgentInstallation).where(AgentInstallation.household_id == household_id,
        AgentInstallation.deployment_id == deployment.id, AgentInstallation.agent_version_id == version.id))
    if installation is None:
        installation = AgentInstallation(household_id=household_id, deployment_id=deployment.id,
                                         agent_version_id=version.id, status="installed", authority_slot="active")
        session.add(installation); session.flush()
    for tool in tools:
        if tool.risk_level != "low":
            continue
        if not session.scalar(select(InstallationPermission).where(
                InstallationPermission.installation_id == installation.id, InstallationPermission.tool_id == tool.id)):
            session.add(InstallationPermission(household_id=household_id, installation_id=installation.id,
                tool_id=tool.id, tool_version=tool.version, granted_by_account_id=owner_id))
    return deployment


def seed_official_household_agents(session: Session, household_id: str, owner_id: str, device_deployment) -> None:
    _seed_catalog(session)
    chef = _seed_official_agent(session, household_id, owner_id, "家庭厨师", CHEF_TOOL_KEYS,
                                HOUSEHOLD_SKILLS["家庭厨师"], HOUSEHOLD_PROMPTS["家庭厨师"])
    planner = _seed_official_agent(session, household_id, owner_id, "家庭计划员", PLANNER_TOOL_KEYS,
                                   HOUSEHOLD_SKILLS["家庭计划员"], HOUSEHOLD_PROMPTS["家庭计划员"])
    encyclopedia = _seed_official_agent(session, household_id, owner_id, "生活百科", ENCYCLOPEDIA_TOOL_KEYS,
                                        HOUSEHOLD_SKILLS["生活百科"], HOUSEHOLD_PROMPTS["生活百科"])
    _seed_official_agent(session, household_id, owner_id, "家庭总管", (), {}, HOUSEHOLD_PROMPTS["家庭总管"],
                         children=(device_deployment.id if device_deployment else None,
                                   chef.id, planner.id, encyclopedia.id),
                         delegation_depth=1)
