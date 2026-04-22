import math
import re
from urllib.parse import urlparse

from flask import request, jsonify

from app.api.router import api, logger
from app.configs import config
from app.extensions.ext_redis import redis_client
from app.models.account import Account, AccountStatus, TenantAccountJoin, TenantAccountRole
from app.models.engine import db
from app.models.model import App, Site
from app.models.organization import Organization
from app.services.auth_context import get_current_user_id, get_current_user_role, is_privileged
from app.services.passport import PassportService

TEAM_REGEX = re.compile(r'\(([^)]+)\)\s*$')


def _extract_origin(req) -> str:
    """Return the bare origin (scheme+host) from Origin header, embed cookie, or Referer."""
    # 1. Origin header (cross-origin deployments)
    origin = req.headers.get("Origin", "").strip()
    if origin:
        return origin.rstrip("/")

    # 2. dify_embed_origin cookie — set by nginx on /chat/ iframe page loads
    #    contains the full Referer URL of the parent page (e.g. https://customer.com/page)
    embed_cookie = req.cookies.get("dify_embed_origin", "").strip()
    if embed_cookie:
        parsed = urlparse(embed_cookie)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    # 3. Referer header fallback
    referer = req.headers.get("Referer", "").strip()
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    return ""


def _is_embed_origin_allowed(req, app_id: str = "") -> bool:
    """Return True if the request's Origin/Referer is in the allowed embed origins."""
    origin = _extract_origin(req)
    if not origin:
        return False

    # Per-app allowlist stored by admin
    if app_id:
        per_app = redis_client.get(f"webapp_embed_origins:{app_id}")
        if per_app:
            for allowed in per_app.decode().split(","):
                if allowed.strip().rstrip("/") == origin:
                    return True

    # Global fallback from env
    global_origins = config.EMBED_ALLOWED_ORIGINS
    if global_origins:
        for allowed in global_origins.split(","):
            if allowed.strip().rstrip("/") == origin:
                return True

    return False


# 조직 레벨 매핑
ORG_LEVEL_LABELS = {1: "company", 2: "division", 3: "department", 4: "team"}


def extract_team(name: str) -> str:
    """Extract team name from user name format: '홍길동(개발팀)' → '개발팀'"""
    match = TEAM_REGEX.search(name or "")
    return match.group(1) if match else ""


def check_permission(app_id: str, user_id: str) -> bool:
    """Check if a user has permission to access an app based on access mode, accounts, and groups."""
    # Owner/admin and the app creator always bypass the access check, regardless
    # of whether access control has been configured on the app.
    if user_id and user_id != "visitor":
        try:
            app = App.get_by_id(app_id)
            if app and str(app.created_by) == str(user_id):
                return True

            join = (
                db.session.query(TenantAccountJoin)
                .filter(
                    TenantAccountJoin.tenant_id == config.TENANT_ID,
                    TenantAccountJoin.account_id == user_id,
                )
                .first()
            )
            if join and join.role in (TenantAccountRole.OWNER, TenantAccountRole.ADMIN):
                return True
        except Exception as e:
            logger.exception("check_permission bypass lookup failed: %s", e)
            db.session.rollback()

    access_mode = "public"
    access_mode_value = redis_client.get(f"webapp_access_mode:{app_id}")
    if access_mode_value is not None:
        access_mode = access_mode_value.decode()

    if access_mode in ("public", "sso_verified") and user_id and user_id != "visitor":
        return True

    # Dify sends 'private' when admin picks "Specific Groups/Members" and
    # 'private_all' when admin picks "Organization". This project treats
    # both as the restrict-to-specific-subjects mode, so the account/group
    # whitelist is enforced regardless of which value the frontend stored.
    if access_mode in ("private", "private_all") and user_id and user_id != "visitor":
        # Check individual accounts
        accounts_value = redis_client.get(f"webapp_access_mode:accounts:{app_id}")
        if accounts_value:
            accounts = [a for a in accounts_value.decode().split(",") if a]
            if user_id in accounts:
                return True

        # Check group membership via organizations table
        groups_value = redis_client.get(f"webapp_access_mode:groups:{app_id}")
        if groups_value:
            group_ids = [g for g in groups_value.decode().split(",") if g]
            user = db.session.query(Account).filter(Account.id == user_id).first()
            if user:
                user_team = extract_team(user.name)
                if user_team:
                    # Get user's full org chain: [팀, 부문, 본부, 회사]
                    org_chain = Organization.get_org_chain_for_team(user_team)
                    for group_id in group_ids:
                        # group_id format: "org:조직명"
                        org_name = group_id.replace("org:", "", 1) if group_id.startswith("org:") else group_id
                        if org_name in org_chain:
                            return True

    return False


@api.get("/info")
def get_enterprise_info():
    logger.info("get_enterprise_info called")
    data = {
        "SSOEnforcedForSignin": True,
        "SSOEnforcedForSigninProtocol": "oidc",
        "SSOEnforcedForWebProtocol": "oidc",
        "EnableEmailCodeLogin": True,
        "EnableEmailPasswordLogin": True,
        "IsAllowRegister": True,
        "IsAllowCreateWorkspace": True,
        "Branding": {
            "applicationTitle": "",
            "loginPageLogo": "",
            "workspaceLogo": "",
            "favicon": "",
        },
        "WebAppAuth": {
            "allowSso": True,
            "allowEmailCodeLogin": True,
            "allowEmailPasswordLogin": True,
        },
        "License": {
            "status": "active",
            "workspaces": {
                "enabled": True,
                "used": 1,
                "limit": 100
            },
            "expiredAt": "2099-12-31T23:59:59Z",
        },
        "PluginInstallationPermission": {
            "pluginInstallationScope": "all",
            "restrictToMarketplaceOnly": True
        }
    }

    return data


@api.get("/sso/app/last-update-time")
@api.get("/sso/workspace/last-update-time")
def get_sso_app_last_update_time():
    return jsonify("2025-01-01T00:00:00+00:00")


@api.post("/webapp/access-mode")
@api.post("/console/api/enterprise/webapp/app/access-mode")
def set_app_access_mode():
    appId = request.json.get("appId", "")
    access_mode = request.json.get("accessMode", "")
    subjects = request.json.get("subjects", [])
    logger.info(f"set_app_access_mode called with appId: {appId}, accessMode: {access_mode}, subjects: {subjects}")

    if appId == "":
        return {"accessMode": "public", "result": False}

    # editor는 본인이 만든 앱에만 접근제어를 변경할 수 있고, owner/admin은 전부 가능
    user_id = get_current_user_id(request)
    role = get_current_user_role(request)
    if not user_id or not role:
        return {"error": "unauthorized", "message": "권한이 없습니다."}, 401
    if not is_privileged(role):
        app = App.get_by_id(appId)
        if not app or str(app.created_by) != str(user_id):
            logger.info("Denying set_app_access_mode: user %s role %s is not owner of app %s", user_id, role, appId)
            return {"error": "forbidden", "message": "권한이 없습니다."}, 403

    accounts = []
    groups = []
    for subject in subjects:
        subject_id = subject.get("subjectId", "")
        subject_type = subject.get("subjectType", "")
        if subject_type == "account":
            accounts.append(subject_id)
        elif subject_type == "group":
            groups.append(subject_id)

    # Per-app embed origin allowlist (only meaningful when accessMode == "public")
    embed_origins = [o.strip() for o in request.json.get("embedAllowedOrigins", []) if o.strip()]

    redis_client.set(f"webapp_access_mode:{appId}", access_mode)
    redis_client.set(f"webapp_access_mode:accounts:{appId}", ",".join(accounts))
    redis_client.set(f"webapp_access_mode:groups:{appId}", ",".join(groups))
    redis_client.set(f"webapp_embed_origins:{appId}", ",".join(embed_origins))

    return {"accessMode": access_mode, "result": True}


@api.get("/webapp/access-mode/id")
@api.get("/api/webapp/access-mode")
@api.get("/console/api/enterprise/webapp/app/access-mode")
def get_app_access_mode():
    app_id = request.args.get("appId", "")
    app_code = request.args.get("appCode", "")
    logger.info(f"get_app_access_mode: app_id={app_id}, app_code={app_code}")

    if app_code != "":
        site = db.session.query(Site).filter(Site.code == app_code).first()
        if site:
            app_id = site.app_id
    if app_id == "":
        logger.info(f"app_id is empty, return private")
        return {"accessMode": "private"}
    else:
        access_mode = redis_client.get(f"webapp_access_mode:{app_id}")
        if access_mode:
            mode = access_mode.decode()
            if mode == "public":
                # Allow unauthenticated embed only from explicitly allowed origins
                if _is_embed_origin_allowed(request, app_id=app_id):
                    logger.info(f"app_id:{app_id}, public embed allowed for origin={_extract_origin(request)}")
                else:
                    mode = "private"
            logger.info(f"app_id:{app_id}, access_mode: {mode}")
            return {"accessMode": mode}
        else:
            logger.info(f"app_id:{app_id}, access_mode not set, return private")
            return {"accessMode": "private"}


@api.post("/webapp/access-mode/batch/id")
def get_webapp_access_mode_code_batch():
    appIds = request.json.get("appIds", [])
    accessModes = {}
    logger.info(f"get_webapp_access_mode_code_batch: appIds={appIds}")

    for app_id in appIds:
        access_mode = redis_client.get(f"webapp_access_mode:{app_id}")
        if access_mode:
            mode = access_mode.decode()
            accessModes[app_id] = "sso_verified" if mode == "public" else mode
        else:
            accessModes[app_id] = "sso_verified"

    return {"accessModes": accessModes}


@api.get("/api/webapp/permission")
@api.get("/console/api/enterprise/webapp/permission")
def get_app_permission():
    app_id = request.args.get("appId", "")
    app_code = request.args.get("appCode", "")
    logger.info(f"get_app_permission: app_id={app_id}, app_code={app_code}")

    if app_code != "":
        site = db.session.query(Site).filter(Site.code == app_code).first()
        if site:
            app_id = site.app_id
        else:
            logger.info(f"app_code {app_code} not found")
            return {"result": False}

    # Accept token from Authorization header (webapp calls) OR access_token
    # cookie (console calls). Dify's console frontend does not always attach
    # Authorization on the enterprise endpoints — it relies on the cookie.
    user_id = get_current_user_id(request)
    if not user_id or user_id == "visitor":
        # Allow visitor on public apps when the request comes from an allowed embed origin
        if app_id:
            access_mode_value = redis_client.get(f"webapp_access_mode:{app_id}")
            access_mode = access_mode_value.decode() if access_mode_value else None
            if access_mode == "public" and _is_embed_origin_allowed(request, app_id=app_id):
                logger.info(f"get_app_permission: visitor allowed for public app {app_id} origin={_extract_origin(request)}")
                return {"result": True}
        return {"error": "unauthorized"}, 401

    result = check_permission(app_id, user_id)
    logger.info(f"app_id {app_id} user_id {user_id} permission: {result}")
    return {"result": result}


@api.get("/console/api/enterprise/webapp/app/subjects")
def get_app_subjects():
    app_id = request.args.get("appId", "")
    logger.info(f"get_app_subjects: app_id={app_id}")

    if app_id == "":
        return {"groups": [], "members": []}

    accounts_value = redis_client.get(f"webapp_access_mode:accounts:{app_id}")
    if accounts_value:
        accounts = accounts_value.decode().split(",")
        users = db.session.query(Account).filter(Account.status == AccountStatus.ACTIVE, Account.id.in_(accounts)).all()
    else:
        users = []

    members = []
    for user in users:
        members.append({
            "id": str(user.id),
            "name": user.name or "",
            "email": user.email or "",
            "avatar": user.avatar or "",
            "avatarUrl": ""
        })

    # Get groups assigned to this app
    groups_value = redis_client.get(f"webapp_access_mode:groups:{app_id}")
    groups = []
    if groups_value:
        group_ids = [g for g in groups_value.decode().split(",") if g]
        for group_id in group_ids:
            # group_id format: "org:조직명"
            org_name = group_id.replace("org:", "", 1) if group_id.startswith("org:") else group_id
            groups.append({
                "id": group_id,
                "name": org_name,
            })

    return {"groups": groups, "members": members}


@api.get("/console/api/enterprise/webapp/app/subject/search")
def search_app_subjects():
    try:
        # Validate and retrieve parameters
        page = max(1, int(request.args.get("pageNumber", 1)))
        page_size = min(100, max(1, int(request.args.get("resultsPerPage", 10))))  # Limit page size
        keyword = request.args.get("keyword", "").strip()
        group_id = request.args.get("groupId", "").strip()
        logger.info(f"search_app_subjects: page={page}, page_size={page_size}, keyword={keyword}, group_id={group_id}")

        # Build base query
        base_query = db.session.query(Account).filter(Account.status == AccountStatus.ACTIVE)

        # Drill-down: restrict to accounts that belong to a specific org (group)
        if group_id:
            org_name = group_id.replace("org:", "", 1) if group_id.startswith("org:") else group_id
            try:
                team_rows = Organization.get_teams_by_org(org_name)
            except Exception as org_error:
                logger.exception("get_teams_by_org failed: %s", org_error)
                db.session.rollback()
                team_rows = []
            team_names = [t[0] for t in team_rows if t and t[0]]
            if team_names:
                base_query = base_query.filter(
                    db.or_(*[Account.name.ilike(f"%({team})%") for team in team_names])
                )
            else:
                # Group resolves to no teams → no members
                base_query = base_query.filter(db.false())

        # Search filter - supports name and email search
        if keyword:
            search_filter = db.or_(
                Account.name.ilike(f"%{keyword}%"),
                Account.email.ilike(f"%{keyword}%")
            )
            base_query = base_query.filter(search_filter)

        # Paginate results with stable ordering
        paginated_query = base_query.order_by(Account.name, Account.id)

        # Get total count
        total_count = base_query.count()

        if total_count == 0:
            return {
                "currPage": page,
                "totalPages": 0,
                "subjects": [],
                "hasMore": False,
            }

        # Paginated query
        offset = (page - 1) * page_size
        users = paginated_query.limit(page_size).offset(offset).all()

        # Build group subjects as a tree from organizations table.
        # Only include groups on the first page; pagination applies to accounts only.
        # Skip groups when drilling into a specific group — caller wants members only.
        group_subjects = []
        if page == 1 and not group_id:
            try:
                org_rows = Organization.get_tree_rows(keyword)
            except Exception as org_error:
                # Table missing or query failed — log and continue with accounts only
                logger.exception("get_tree_rows failed: %s", org_error)
                db.session.rollback()
                org_rows = []

            # Map DB id → "org:<name>" so parentGroupId points at a group we return.
            id_to_group: dict[str, str] = {
                row.id: f"org:{row.org_name}" for row in org_rows
            }
            included_group_ids: set[str] = set()
            for row in org_rows:
                group_id = f"org:{row.org_name}"
                # Same org_name can appear on multiple rows (company/division share a name);
                # keep only the first so the tree has no duplicate nodes.
                if group_id in included_group_ids:
                    continue
                included_group_ids.add(group_id)

                parent_group_id = id_to_group.get(row.parent_id) if row.parent_id else None
                group_subjects.append({
                    "subjectId": group_id,
                    "subjectType": "group",
                    "groupData": {
                        "id": group_id,
                        "name": row.org_name,
                        "parentGroupId": parent_group_id,
                    }
                })

        # Build account subjects
        account_subjects = [
            {
                "subjectId": str(user.id),
                "subjectType": "account",
                "accountData": {
                    "id": str(user.id),
                    "name": user.name or "",
                    "email": user.email or "",
                    "avatar": user.avatar or "",
                    "avatarUrl": ""
                }
            }
            for user in users
        ]

        # Groups first, then accounts
        subjects = group_subjects + account_subjects

        # Calculate pagination info (accounts only for pagination)
        total_pages = math.ceil(total_count / page_size)
        has_more = page < total_pages

        return {
            "currPage": page,
            "totalPages": total_pages,
            "subjects": subjects,
            "hasMore": has_more,
        }

    except ValueError as e:
        # Parameter type error
        return {
            "error": "Invalid parameter format",
            "message": "pageNumber and resultsPerPage must be valid integers"
        }, 400
    except Exception as e:
        # Other exceptions
        logger.exception("search_app_subjects failed: %s", e)
        db.session.rollback()
        return {
            "error": "Internal server error",
            "message": "An error occurred while searching subjects"
        }, 500


@api.get("/webapp/access-mode/code")
def get_webapp_access_mode_code():
    logger.info("get_webapp_access_mode_code called", request.args)
    app_code = request.args.get("app_code", "")
    if app_code == "":
        app_code = request.args.get("appCode", "")

    logger.info(f"get_webapp_access_mode_code: app_code={app_code}")

    if app_code == "":
        logger.info(f"app_code is empty, return private")
        return {"accessMode": "private"}

    site = db.session.query(Site).filter(Site.code == app_code).first()
    if site:
        app_id = str(site.app_id)
        access_mode_value = redis_client.get(f"webapp_access_mode:{app_id}")
        if access_mode_value:
            mode = access_mode_value.decode()
            if mode == "public":
                if _is_embed_origin_allowed(request, app_id=app_id):
                    logger.info(f"app_code:{app_code}, public embed allowed for origin={_extract_origin(request)}")
                else:
                    mode = "private"
            logger.info(f"app_code:{app_code}, access_mode: {mode}")
            return {"accessMode": mode}
        else:
            logger.info(f"app_code:{app_code}, access_mode not set, return private")
            return {"accessMode": "private"}
    else:
        logger.info(f"app_code {app_code} not found, return private")
        return {"accessMode": "private"}


@api.get("/webapp/permission")
def get_webapp_permission():
    app_code = request.args.get("appCode", "")
    user_id = request.args.get("userId", "")
    app_id = request.args.get("appId", "")
    logger.info(f"get_webapp_permission: app_code={app_code}, user_id={user_id}")

    if app_code != "":
        site = db.session.query(Site).filter(Site.code == app_code).first()
        if site:
            app_id = site.app_id
        else:
            logger.info(f"app_code {app_code} not found")
            return {"result": False}

    if not user_id or user_id == "visitor":
        # Allow visitor on public apps when the request comes from an allowed embed origin
        if app_id:
            access_mode_value = redis_client.get(f"webapp_access_mode:{app_id}")
            access_mode = access_mode_value.decode() if access_mode_value else None
            if access_mode == "public" and _is_embed_origin_allowed(request, app_id=app_id):
                logger.info(f"get_webapp_permission: visitor allowed for public app {app_id} origin={_extract_origin(request)}")
                return {"result": True}
        return {"error": "unauthorized"}, 401

    result = check_permission(app_id, user_id)
    logger.info(f"get_webapp_permission: app_id {app_id} user_id {user_id} permission: {result}")
    return {"result": result}


@api.post("/webapp/permission/batch")
def get_webapp_permission_batch():
    appCodes = request.json.get("appCodes", [])
    userId = request.json.get("userId", "")
    permissions = {}
    logger.info(f"get_webapp_permission_batch: appCodes={appCodes}, userId={userId}")

    for app_code in appCodes:
        permissions[app_code] = False
        site = db.session.query(Site).filter(Site.code == app_code).first()
        if site:
            app_id = site.app_id
        else:
            continue

        permissions[app_code] = check_permission(app_id, userId)

    return {"permissions": permissions}


@api.delete("/webapp/clean")
def clean_webapp_access_mode():
    appId = request.args.get("appId", "")
    logger.info(f"clean_webapp_access_mode called with appId: {appId}")

    if appId == "":
        return {"result": False}
    logger.info(f"clean_webapp_access_mode: {appId}")
    redis_client.delete(f"webapp_access_mode:{appId}")
    redis_client.delete(f"webapp_access_mode:groups:{appId}")
    redis_client.delete(f"webapp_access_mode:accounts:{appId}")
    redis_client.delete(f"webapp_embed_origins:{appId}")

    return {"result": True}


# PluginManagerService
@api.post("/check-credential-policy-compliance")
def check_credential_policy_compliance():
    # Example request body
    # {'dify_credential_id': '0198eabb-3b2c-793e-a491-3ddf5bfc75a6', 'provider': 'langgenius/tongyi/tongyi', 'credential_type': 0}
    data = request.json
    logger.info(f"check_credential_policy_compliance called with data: {data}")

    return {"result": True}
