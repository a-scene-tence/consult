"""JWT 인증·권한 (Phase 6).

`OAuth2PasswordBearer` + `PyJWT` 기반 JWT 인증. 두 역할로 접근을 분리한다.
- **admin(전문가)**: 모든 API(고객 등록·업로드·분석·피드백·승인).
- **client(사장님)**: 본인 `client_id` 의 대시보드 조회만.

계정은 **테스트/데모용 하드코딩**이다. 실제 운영에서는 사용자 테이블 + 해시 비밀번호로 대체해야
한다(비밀번호 평문 하드코딩 금지). `JWT_SECRET` 은 환경변수로 주입한다.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

_SECRET = os.environ.get("JWT_SECRET", "dev-only-insecure-secret-change-me")
_ALGORITHM = "HS256"
_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "120"))

# 데모/테스트 전용 하드코딩 계정. 운영은 DB + 해시로 대체.
# client 계정은 특정 client_id 에 바인딩된다(본인 대시보드만 조회).
_USERS: dict[str, dict[str, Any]] = {
    "admin": {"password": "admin-secret", "role": "admin"},
    "owner": {"password": "owner-secret", "role": "client", "client_id": 1},
}

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """자격 검증(상수시간 비교). 실패 시 None."""
    user = _USERS.get(username)
    if user is None:
        return None
    if not hmac.compare_digest(password, user["password"]):
        return None
    return {"username": username, **{k: v for k, v in user.items() if k != "password"}}


def create_access_token(user: dict[str, Any]) -> str:
    """user(dict)로 JWT 발급. role·client_id 클레임 포함."""
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=_EXPIRE_MINUTES),
    }
    if "client_id" in user:
        claims["client_id"] = user["client_id"]
    return jwt.encode(claims, _SECRET, algorithm=_ALGORITHM)


_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="자격 증명이 유효하지 않습니다.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
    """Bearer 토큰을 해석해 현재 사용자 클레임을 반환(실패 시 401)."""
    try:
        payload = jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise _CREDENTIALS_EXC from exc
    if "sub" not in payload or "role" not in payload:
        raise _CREDENTIALS_EXC
    return payload


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """관리자(전문가) 전용 가드."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 권한이 필요합니다.")
    return user


def authorize_dashboard(client_id: int, user: dict[str, Any]) -> None:
    """대시보드 접근 인가 — admin 은 전체, client 는 본인 client_id 만."""
    if user.get("role") == "admin":
        return
    if user.get("role") == "client" and user.get("client_id") == client_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="본인 대시보드만 조회할 수 있습니다.")
