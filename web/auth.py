"""JWT 인증·권한 (Phase 6·7).

`OAuth2PasswordBearer` + `PyJWT` 기반 JWT 인증. 비밀번호는 **passlib[bcrypt]** 로 해싱하고,
계정은 **`users` 테이블**에서 검증한다(하드코딩 폐기). 두 역할로 접근을 분리한다.
- **admin(전문가)**: 모든 API.
- **client(사장님)**: 본인 `client_id` 대시보드 조회만.

`JWT_SECRET` 은 환경변수로 주입한다. 기본 시드 계정은 `seed_default_users`(데모/부트스트랩)로 생성한다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext

from db.models import User

_SECRET = os.environ.get("JWT_SECRET", "dev-only-insecure-secret-change-me")
_ALGORITHM = "HS256"
_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "120"))

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


# --- 비밀번호 해싱 ---
def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd.verify(password, password_hash)
    except ValueError:
        return False


# --- DB 인증 ---
def authenticate(session_factory: Any, username: str, password: str) -> dict[str, Any] | None:
    """users 테이블에서 자격을 검증. 실패 시 None."""
    with session_factory() as session:
        user = session.query(User).filter_by(username=username).one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            return None
        payload: dict[str, Any] = {"username": user.username, "role": user.role}
        if user.client_id is not None:
            payload["client_id"] = user.client_id
        return payload


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


# --- 시드(부트스트랩/데모) ---
def upsert_user(
    session_factory: Any, username: str, password: str, role: str, client_id: int | None = None
) -> None:
    """사용자를 생성(존재 시 비밀번호/역할 갱신)."""
    with session_factory() as session:
        user = session.query(User).filter_by(username=username).one_or_none()
        if user is None:
            user = User(username=username)
            session.add(user)
        user.password_hash = hash_password(password)
        user.role = role
        user.client_id = client_id
        session.commit()


def seed_default_users(session_factory: Any) -> None:
    """데모/테스트용 기본 계정 — 비밀번호는 환경변수로 재정의 가능.

    운영에서는 이 시드 대신 안전한 비밀번호로 사용자를 생성해야 한다.
    """
    upsert_user(session_factory, "admin",
                os.environ.get("ADMIN_PASSWORD", "admin-secret"), "admin")
    upsert_user(session_factory, "owner",
                os.environ.get("OWNER_PASSWORD", "owner-secret"), "client", client_id=1)
