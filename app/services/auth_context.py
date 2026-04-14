"""Helpers for extracting the current console user and role from a request."""
import logging
from typing import Optional

from flask import Request

from app.configs import config
from app.extensions.ext_database import db
from app.models.account import TenantAccountJoin, TenantAccountRole
from app.services.passport import PassportService
from app.services.token import COOKIE_NAME_ACCESS_TOKEN, TokenService

logger = logging.getLogger(__name__)


def _extract_token(request: Request) -> Optional[str]:
    """Pull the console access token from Authorization header or cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header and " " in auth_header:
        scheme, tk = auth_header.split(None, 1)
        if scheme.lower() == "bearer" and tk:
            return tk

    cookie_name = TokenService.real_cookie_name(COOKIE_NAME_ACCESS_TOKEN)
    token = request.cookies.get(cookie_name) or request.cookies.get(COOKIE_NAME_ACCESS_TOKEN)
    return token or None


def get_current_user_id(request: Request) -> Optional[str]:
    """Return the authenticated console user's account id, or None."""
    token = _extract_token(request)
    if not token:
        return None
    try:
        decoded = PassportService().verify(token)
    except Exception as e:
        logger.debug("Token verification failed: %s", e)
        return None
    return decoded.get("user_id") or None


def get_current_user_role(request: Request) -> Optional[str]:
    """Return the user's TenantAccountRole within the configured tenant."""
    user_id = get_current_user_id(request)
    if not user_id:
        return None
    join = (
        db.session.query(TenantAccountJoin)
        .filter(
            TenantAccountJoin.tenant_id == config.TENANT_ID,
            TenantAccountJoin.account_id == user_id,
        )
        .first()
    )
    return join.role if join else None


def is_privileged(role: Optional[str]) -> bool:
    """Owner/admin bypass most ownership checks."""
    return role in {TenantAccountRole.OWNER, TenantAccountRole.ADMIN}
