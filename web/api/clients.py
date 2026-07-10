"""고객(CRM) 등록 API (DESIGN.md A-0.5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from web.api import get_session_factory
from web.auth import require_admin
from web.services import create_client, list_clients

router = APIRouter(prefix="/api/clients", tags=["clients"])


class ClientIn(BaseModel):
    """고객 등록 요청 — 대표자명 + client_profile 필드(느슨히 허용)."""

    model_config = ConfigDict(extra="allow")

    name: str
    industry: str
    district_type: str
    owner_gender: str
    owner_age_band: str
    risk_appetite: str


@router.post("", status_code=201)
def register_client(
    body: ClientIn, request: Request, _admin: dict = Depends(require_admin)
) -> dict[str, Any]:
    client_id = create_client(get_session_factory(request), body.model_dump(exclude_none=True))
    return {"client_id": client_id}


@router.get("")
def get_clients(
    request: Request, _admin: dict = Depends(require_admin)
) -> list[dict[str, Any]]:
    """조종석 고객 드롭다운용 목록 — [{id(정수), client_id('C-{id}'), name}]. admin 전용."""
    return list_clients(get_session_factory(request))
