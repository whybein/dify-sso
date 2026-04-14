"""Console app list filtering + publish/edit restrictions.

Reverse-proxies selected /console/api/apps endpoints to Dify API so we can
filter the response and enforce org-based access + editor ownership rules.
"""
from typing import Optional

import requests
from flask import Response, request

from app.api.dify.webapp import ORG_LEVEL_LABELS, check_permission, extract_team  # noqa: F401
from app.api.router import api, logger
from app.configs import config
from app.extensions.ext_database import db
from app.extensions.ext_redis import redis_client
from app.models.account import Account, TenantAccountRole
from app.models.model import App
from app.models.organization import Organization
from app.services.auth_context import get_current_user_id, get_current_user_role, is_privileged

REQUEST_TIMEOUT = 30
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length",
}


def _forward(method: str, path: str) -> requests.Response:
    """Forward the current Flask request to the Dify API backend."""
    base = (config.DIFY_API_INTERNAL_URL or "").rstrip("/")
    if not base:
        raise RuntimeError("DIFY_API_INTERNAL_URL is not configured")

    url = f"{base}{path}"
    # Strip headers that would confuse the upstream server.
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}}

    return requests.request(
        method=method,
        url=url,
        headers=headers,
        params=request.args,
        data=request.get_data(),
        cookies=request.cookies,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=False,
    )


def _passthrough_response(upstream: requests.Response) -> Response:
    """Return the upstream response verbatim, stripping hop-by-hop headers."""
    headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS]
    return Response(upstream.content, status=upstream.status_code, headers=headers)


def _user_org_chain(user_id: str) -> list[str]:
    """Return the full org chain names for the given user (by team name in display name)."""
    user = db.session.query(Account).filter(Account.id == user_id).first()
    if not user:
        return []
    team = extract_team(user.name or "")
    if not team:
        return []
    return Organization.get_org_chain_for_team(team)


def _app_accessible(app_id: str, user_id: str, role: str, org_chain: list[str]) -> bool:
    """True when the user may see this app in the console list."""
    if is_privileged(role):
        return True

    # The creator always sees their own app.
    app = App.get_by_id(app_id)
    if app and str(app.created_by) == str(user_id):
        return True

    access_mode_value = redis_client.get(f"webapp_access_mode:{app_id}")
    access_mode = access_mode_value.decode() if access_mode_value else "public"

    # 'private' (Specific Groups/Members) and 'private_all' (Organization) are
    # both treated as restrict-to-whitelist in this project. Everything else
    # (public, sso_verified, default) is visible to any authenticated user.
    if access_mode not in ("private", "private_all"):
        return True

    accounts_value = redis_client.get(f"webapp_access_mode:accounts:{app_id}")
    if accounts_value:
        accounts = [a for a in accounts_value.decode().split(",") if a]
        if user_id in accounts:
            return True

    groups_value = redis_client.get(f"webapp_access_mode:groups:{app_id}")
    if groups_value and org_chain:
        group_ids = [g for g in groups_value.decode().split(",") if g]
        for group_id in group_ids:
            org_name = group_id.replace("org:", "", 1) if group_id.startswith("org:") else group_id
            if org_name in org_chain:
                return True

    return False


def _require_ownership(app_id: str) -> Optional[tuple[dict, int]]:
    """Return (body, status) when the caller is not allowed to modify this app, else None."""
    user_id = get_current_user_id(request)
    role = get_current_user_role(request)
    if not user_id or not role:
        return {"error": "unauthorized", "message": "권한이 없습니다."}, 401
    if is_privileged(role):
        return None
    app = App.get_by_id(app_id)
    if not app or str(app.created_by) != str(user_id):
        logger.info("Denying console mutation: user %s role %s not owner of app %s", user_id, role, app_id)
        return {"error": "forbidden", "message": "권한이 없습니다."}, 403
    return None


@api.get("/console/api/apps")
def list_apps():
    """Proxy /console/api/apps and filter results by access control rules."""
    user_id = get_current_user_id(request)
    role = get_current_user_role(request)
    if not user_id or not role:
        return {"error": "unauthorized"}, 401

    try:
        upstream = _forward("GET", "/console/api/apps")
    except Exception as e:
        logger.exception("Failed to proxy /console/api/apps: %s", e)
        return {"error": "upstream_unavailable"}, 502

    if upstream.status_code != 200:
        return _passthrough_response(upstream)

    try:
        payload = upstream.json()
    except ValueError:
        return _passthrough_response(upstream)

    # owner/admin 은 필터링 생략
    if is_privileged(role):
        return payload, upstream.status_code

    org_chain = _user_org_chain(user_id)
    data = payload.get("data", [])
    filtered = [
        item for item in data
        if _app_accessible(str(item.get("id", "")), user_id, role, org_chain)
    ]

    removed = len(data) - len(filtered)
    if removed:
        logger.debug("Filtered %d app(s) for user %s (role %s)", removed, user_id, role)
    payload["data"] = filtered
    if "total" in payload:
        payload["total"] = max(0, payload["total"] - removed)
    return payload, upstream.status_code


@api.get("/console/api/apps/<string:app_id>")
def get_app(app_id: str):
    user_id = get_current_user_id(request)
    role = get_current_user_role(request)
    if not user_id or not role:
        return {"error": "unauthorized"}, 401

    if not is_privileged(role):
        org_chain = _user_org_chain(user_id)
        if not _app_accessible(app_id, user_id, role, org_chain):
            return {"error": "forbidden"}, 403

    try:
        upstream = _forward("GET", f"/console/api/apps/{app_id}")
    except Exception as e:
        logger.exception("Failed to proxy /console/api/apps/%s: %s", app_id, e)
        return {"error": "upstream_unavailable"}, 502
    return _passthrough_response(upstream)


def _mutate_app(app_id: str, subpath: str, method: str):
    denial = _require_ownership(app_id)
    if denial is not None:
        body, status = denial
        return body, status
    try:
        upstream = _forward(method, f"/console/api/apps/{app_id}{subpath}")
    except Exception as e:
        logger.exception("Failed to proxy %s /console/api/apps/%s%s: %s", method, app_id, subpath, e)
        return {"error": "upstream_unavailable"}, 502
    return _passthrough_response(upstream)


# Editor restrictions: only the app creator (or owner/admin) may mutate these.
@api.delete("/console/api/apps/<string:app_id>")
def delete_app(app_id: str):
    return _mutate_app(app_id, "", "DELETE")


@api.put("/console/api/apps/<string:app_id>")
def update_app(app_id: str):
    return _mutate_app(app_id, "", "PUT")


@api.post("/console/api/apps/<string:app_id>/workflows/publish")
def publish_app_workflow(app_id: str):
    return _mutate_app(app_id, "/workflows/publish", "POST")


# --- Pass-through for unhandled /console/api/apps paths ---
# The Ingress sends the full /console/api/apps Prefix here, so we must forward
# any request we do not explicitly restrict (app creation, workflow drafts, site
# settings, model-config, etc.) back to Dify unchanged. Flask picks the most
# specific matching rule first, so the routes above still win when they apply.

def _proxy_passthrough(path: str):
    try:
        upstream = _forward(request.method, path)
    except Exception as e:
        logger.exception("Failed to proxy %s %s: %s", request.method, path, e)
        return {"error": "upstream_unavailable"}, 502
    return _passthrough_response(upstream)


@api.post("/console/api/apps")
def create_app():
    return _proxy_passthrough("/console/api/apps")


@api.route(
    "/console/api/apps/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
def passthrough_app_subpath(subpath: str):
    # Block non-permitted users from reaching ANY /console/api/apps/<id>/*
    # endpoint. Without this gate the console frontend would still be able to
    # render peripheral panels (api-keys, monitoring, etc.) because those
    # requests would flow straight through to Dify.
    user_id = get_current_user_id(request)
    role = get_current_user_role(request)
    if not user_id or not role:
        return {"error": "unauthorized"}, 401

    if not is_privileged(role):
        app_id = subpath.split("/", 1)[0]
        if app_id:
            org_chain = _user_org_chain(user_id)
            if not _app_accessible(app_id, user_id, role, org_chain):
                logger.info(
                    "Denying passthrough: user %s has no access to app %s (%s %s)",
                    user_id, app_id, request.method, subpath,
                )
                return {"error": "forbidden"}, 403

    return _proxy_passthrough(f"/console/api/apps/{subpath}")
