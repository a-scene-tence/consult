"""인증 API — 토큰 발급 (Phase 6)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from web.api import get_session_factory
from web.auth import authenticate, create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends()) -> dict[str, Any]:
    """OAuth2 password flow — username/password 로 JWT 발급(users 테이블 검증)."""
    user = authenticate(get_session_factory(request), form.username, form.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"access_token": create_access_token(user), "token_type": "bearer", "role": user["role"]}
