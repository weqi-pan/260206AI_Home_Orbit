from sqlalchemy import select

from backend.app.db.models import AgentDeployment, AgentInstallation, InstallationPermission, ToolDefinition


class InstallationService:
    def __init__(self, session, household_id, principal_id):
        self.session = session
        self.household_id = household_id
        self.principal_id = principal_id

    def install(self, deployment_id):
        deployment = self.session.scalar(select(AgentDeployment).where(AgentDeployment.id == deployment_id,
                                                                       AgentDeployment.household_id == self.household_id))
        if not deployment:
            raise RuntimeError("PERMISSION_REQUIRED")
        row = self.session.scalar(select(AgentInstallation).where(AgentInstallation.household_id == self.household_id,
                                                                  AgentInstallation.deployment_id == deployment_id))
        if row is None:
            row = AgentInstallation(household_id=self.household_id, deployment_id=deployment.id,
                                    agent_version_id=deployment.agent_version_id)
            self.session.add(row); self.session.commit(); self.session.refresh(row)
        return row

    def grant(self, installation_id, tool_id, tool_version):
        installation = self.session.scalar(select(AgentInstallation).where(AgentInstallation.id == installation_id,
                                                                           AgentInstallation.household_id == self.household_id))
        tool = self.session.get(ToolDefinition, tool_id)
        if not installation or not tool or tool.version != tool_version:
            raise RuntimeError("PERMISSION_REQUIRED")
        row = self.session.scalar(select(InstallationPermission).where(
            InstallationPermission.installation_id == installation_id, InstallationPermission.tool_id == tool_id))
        if row is None:
            row = InstallationPermission(household_id=self.household_id, installation_id=installation_id,
                                         tool_id=tool_id, tool_version=tool_version,
                                         granted_by_account_id=self.principal_id)
            self.session.add(row)
        else:
            row.tool_version = tool_version; row.granted_by_account_id = self.principal_id
        self.session.commit(); return row

    def authorized_tool_ids(self, installation_id):
        return set(self.session.scalars(select(InstallationPermission.tool_id).where(
            InstallationPermission.household_id == self.household_id,
            InstallationPermission.installation_id == installation_id)).all())
