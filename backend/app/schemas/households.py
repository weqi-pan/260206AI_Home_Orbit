from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MemberResponse(BaseModel):
    account_id: str
    username: str
    role: str
    model_config = ConfigDict(from_attributes=True)


class HouseholdResponse(BaseModel):
    id: str
    name: str
    role: str
    members: list[MemberResponse]


class InvitationCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    role: str = Field(pattern="^(admin|member|viewer)$")


class InvitationResponse(BaseModel):
    id: str
    household_id: str
    invited_account_id: str
    role: str
    status: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MembershipResponse(BaseModel):
    household_id: str
    role: str
