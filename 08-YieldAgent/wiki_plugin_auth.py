import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def require_plugin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    configured = os.getenv("OBSIDIAN_PLUGIN_API_TOKEN", "")
    supplied = credentials.credentials if credentials else ""
    valid_scheme = bool(credentials and credentials.scheme.lower() == "bearer")
    if not configured or not valid_scheme or not secrets.compare_digest(configured, supplied):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Obsidian Plugin 인증에 실패했습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
