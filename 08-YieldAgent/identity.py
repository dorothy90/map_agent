import hashlib
import hmac

from fastapi import HTTPException, Request
from pydantic import BaseModel

from settings import get_settings


class PlatformIdentity(BaseModel):
    owner_id: str
    owner_hash: str


def get_platform_identity(request: Request) -> PlatformIdentity:
    settings = get_settings()
    owner_id = request.headers.get(settings.platform_user_id_header, "").strip()
    if not owner_id:
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_REQUIRED"})
    secret = settings.owner_hash_key
    if not secret:
        raise HTTPException(status_code=503, detail={"code": "IDENTITY_CONFIG_ERROR"})
    key = secret.get_secret_value()
    digest = hmac.new(key.encode(), owner_id.encode(), hashlib.sha256).hexdigest()[:24]
    return PlatformIdentity(owner_id=owner_id, owner_hash=digest)
