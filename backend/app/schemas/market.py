# -*- coding: utf-8 -*-
"""Phase 7 market/review request schemas."""
from typing import Literal

from pydantic import BaseModel, Field


class SubmitReviewRequest(BaseModel):
    category: str = Field(default="生活", max_length=32)


class ReviewDecisionRequest(BaseModel):
    action: Literal["approve", "reject", "return", "re-review"]
    reason: str = Field(default="", max_length=500)


class TakedownRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ReportHandleRequest(BaseModel):
    action: Literal["resolve", "dismiss"]
    note: str = Field(default="", max_length=500)


class MarketInstallRequest(BaseModel):
    granted_tool_keys: list[str] = Field(default_factory=list, max_length=32)


class MarketUpgradeRequest(BaseModel):
    granted_tool_keys: list[str] = Field(default_factory=list, max_length=32)


class MarketReportRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
