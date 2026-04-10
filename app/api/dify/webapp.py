import math
import re

from flask import request, jsonify

from app.api.router import api, logger
from app.extensions.ext_redis import redis_client
from app.models.account import Account, AccountStatus
from app.models.engine import db
from app.models.model import Site
from app.models.organization import Organization
from app.services.passport import PassportService

# 이름에서 팀명 추출: "홍길동(개발팀)" → "개발팀"
TEAM_REGEX = re.compile(r'\(([^)]+)\)\s*$')

# 조직 레벨 매핑
ORG_LEVEL_LABELS = {1: "company", 2: "division", 3: "department", 4: "team"}


def extract_team(name: str) -> str:
    """Extract team name from user name format: '홍길동(개발팀)' → '개발팀'"""
    match = TEAM_REGEX.search(name or "")
    return match.group(1) if match else ""


def check_permission(app_id: str, user_id: str) -> bool:
    """Check if a user has permission to access an app based on access mode, accounts, and groups."""
    access_mode = "public"
    access_mode_value = redis_client.get(f"webapp_access_mode:{app_id}")
    if access_mode_value is not None:
        access_mode = access_mode_value.decode()

    if access_mode == "public":
        return user_id and user_id != "visitor"

    if access_mode == "sso_verified" and user_id and user_id != "visitor":
        return True

    if access_mode == "private_all" and user_id and user_id != "visitor":
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

    accounts = []
    groups = []
    for subject in subjects:
        subject_id = subject.get("subjectId", "")
        subject_type = subject.get("subjectType", "")
        if subject_type == "account":
            accounts.append(subject_id)
        elif subject_type == "group":
            groups.append(subject_id)

    redis_client.set(f"webapp_access_mode:{appId}", access_mode)
    redis_client.set(f"webapp_access_mode:accounts:{appId}", ",".join(accounts))
    redis_client.set(f"webapp_access_mode:groups:{appId}", ",".join(groups))

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
        logger.info(f"app_id is empty, return public")
        return {"accessMode": "public"}
    else:
        access_mode = redis_client.get(f"webapp_access_mode:{app_id}")
        if access_mode:
            logger.info(f"app_id:{app_id}, access_mode: {access_mode.decode()}")
            return {"accessMode": access_mode.decode()}
        else:
            logger.info(f"app_id:{app_id}, access_mode not set, return public")
            return {"accessMode": "public"}


@api.post("/webapp/access-mode/batch/id")
def get_webapp_access_mode_code_batch():
    appIds = request.json.get("appIds", [])
    accessModes = {}
    logger.info(f"get_webapp_access_mode_code_batch: appIds={appIds}")

    for app_id in appIds:
        access_mode = redis_client.get(f"webapp_access_mode:{app_id}")
        if access_mode:
            accessModes[app_id] = access_mode.decode()
        else:
            accessModes[app_id] = "public"

    return {"accessModes": accessModes}


@api.get("/api/webapp/permission")
@api.get("/console/api/enterprise/webapp/permission")
def get_app_permission():
    user_id = "visitor"
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

    try:
        auth_header = request.headers.get("Authorization")
        if auth_header is None:
            raise ValueError("Missing Authorization header")
        if " " not in auth_header:
            raise ValueError("Malformed Authorization header")

        auth_scheme, tk = auth_header.split(None, 1)
        if auth_scheme.lower() != "bearer":
            raise ValueError("Unsupported auth scheme")

        decoded = PassportService().verify(tk)
        user_id = decoded.get("end_user_id", decoded.get("user_id", "visitor"))
        logger.debug("app_id %s token validated for user_id: %s", app_id, user_id)
    except Exception:
        logger.debug("app_id %s: no valid token, treating as visitor", app_id)

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
        logger.info(f"search_app_subjects: page={page}, page_size={page_size}, keyword={keyword}")

        # Build base query
        base_query = db.session.query(Account).filter(Account.status == AccountStatus.ACTIVE)

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

        # Build group subjects from organizations table
        group_subjects = []
        if page == 1:
            orgs = Organization.search_orgs(keyword)
            for org_name, org_level in orgs:
                level_label = ORG_LEVEL_LABELS.get(org_level, "")
                group_id = f"org:{org_name}"
                group_subjects.append({
                    "subjectId": group_id,
                    "subjectType": "group",
                    "groupData": {
                        "id": group_id,
                        "name": f"{org_name} ({level_label})" if level_label else org_name,
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
        logger.info(f"app_code is empty, return public")
        return {"accessMode": "public"}

    site = db.session.query(Site).filter(Site.code == app_code).first()
    if site:
        access_mode_value = redis_client.get(f"webapp_access_mode:{site.app_id}")
        if access_mode_value:
            logger.info(f"app_code:{app_code}, access_mode: {access_mode_value.decode()}")
            return {"accessMode": access_mode_value.decode()}
        else:
            logger.info(f"app_code:{app_code}, access_mode not set, return public")
            return {"accessMode": "public"}
    else:
        logger.info(f"app_code {app_code} not found, return public")
        return {"accessMode": "public"}


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

    return {"result": True}


# PluginManagerService
@api.post("/check-credential-policy-compliance")
def check_credential_policy_compliance():
    # Example request body
    # {'dify_credential_id': '0198eabb-3b2c-793e-a491-3ddf5bfc75a6', 'provider': 'langgenius/tongyi/tongyi', 'credential_type': 0}
    data = request.json
    logger.info(f"check_credential_policy_compliance called with data: {data}")

    return {"result": True}
