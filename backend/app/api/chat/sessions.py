from backend.app.api.chat.base import *
from backend.app.api.chat.base import official_device_deployment, official_household_deployment, owned_session, session_dict, exactly_one_household

@router.get("/sessions")
def list_sessions(account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    items = db.scalars(select(ChatSession).where(ChatSession.account_id == account.id).order_by(ChatSession.updated_at.desc())).all()
    return [session_dict(item) for item in items]


def official_household_deployment(db, household_id, deployment_id=None):
    """已安装官方 Agent 的 deployment；可显式指定，默认回退设备管家。"""
    statement = select(AgentDeployment).join(AgentVersion,
        AgentVersion.id == AgentDeployment.agent_version_id).join(AgentDefinition,
        AgentDefinition.id == AgentVersion.agent_id).join(AgentInstallation,
        AgentInstallation.deployment_id == AgentDeployment.id).where(
            AgentDeployment.household_id == household_id,
            AgentInstallation.household_id == household_id, AgentInstallation.status == "installed",
            AgentInstallation.authority_slot == "active",
            AgentInstallation.agent_version_id == AgentDeployment.agent_version_id)
    if deployment_id:
        statement = statement.where(AgentDeployment.id == deployment_id)
    else:
        statement = statement.where(AgentDefinition.name == "设备管家")
    return db.scalar(statement.order_by(AgentDeployment.id))


@router.post("/sessions", status_code=201)
def create_session(payload: SessionCreate, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    household_id = exactly_one_household(db.scalars(select(HouseholdMember.household_id).where(
        HouseholdMember.account_id == account.id)).all())
    deployment = official_device_deployment(db, household_id)
    if payload.deployment_id:
        deployment = official_household_deployment(db, household_id, payload.deployment_id)
        if not deployment:
            raise HTTPException(404, detail={"code": "DEPLOYMENT_NOT_FOUND"})
    item = ChatSession(account_id=account.id, household_id=household_id,
                       deployment_id=deployment.id if deployment else None, title=payload.title)
    db.add(item)
    db.commit()
    db.refresh(item)
    return session_dict(item)


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, payload: SessionUpdate, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    item = owned_session(db, session_id, account.id)
    item.title = payload.title
    db.commit()
    db.refresh(item)
    return session_dict(item)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, response: Response, account: Account = Depends(get_current_account), db: Session = Depends(get_db)):
    item = owned_session(db, session_id, account.id)
    db.execute(update(Ticket).where(Ticket.session_id == item.id).values(session_id=None))
    db.execute(delete(ConversationHistory).where(ConversationHistory.session_id == item.id))
    db.delete(item)
    db.commit()
    response.status_code = 204
