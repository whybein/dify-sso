import logging
import secrets

from flask import request, redirect

from app.api.router import api
from app.configs import config
from app.extensions.ext_oidc import oidc_service
from app.extensions.ext_redis import redis_client
from app.libs.helper import extract_remote_ip
from app.services.account import AccountService
from app.services.token import TokenService

logger = logging.getLogger(__name__)


@api.get("/signin")
def signin_redirect():
    """Redirect /signin to SSO login (replaces Nginx server-snippet)."""
    return redirect("/console/api/enterprise/sso/oidc/login?is_login=true")


@api.get("/console/api/enterprise/sso/oidc/login")
def oidc_login():
    is_login = request.args.get("is_login", False)
    login_url, state = oidc_service.get_login_url()
    if is_login:
        return redirect(login_url)
    else:
        return {"url": login_url}


@api.get("/console/api/enterprise/sso/oidc/callback")
def oidc_callback():
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    redirect_url = request.args.get("redirect_url", "")
    app_code = request.args.get("app_code", "")

    remote_ip = extract_remote_ip(request)

    try:
        # Validate OAuth state (CSRF protection) and retrieve PKCE verifier
        nonce, code_verifier = oidc_service.validate_state(state)

        if app_code and redirect_url:
            # Validate redirect_url against allowed domain
            if not redirect_url.startswith(config.CONSOLE_WEB_URL):
                return {"error": "Authentication failed"}, 400

            tokens = oidc_service.handle_callback(
                code, remote_ip, code_verifier,
                f"app_code={app_code}&redirect_url={redirect_url}", app_code
            )

            # Use short-lived code instead of passing token directly in URL
            short_code = secrets.token_urlsafe(32)
            from datetime import timedelta
            redis_client.setex(
                f"webapp_sso_code:{short_code}",
                timedelta(minutes=2),
                tokens['access_token']
            )

            return redirect(
                f"{config.CONSOLE_WEB_URL}/webapp-signin?web_sso_code={short_code}&redirect_url={redirect_url}"
            )
        else:
            account = oidc_service.bind_account(code, remote_ip, code_verifier)
            token_pair = AccountService.login(account, remote_ip)

            response = redirect(f"{config.CONSOLE_WEB_URL}")

            TokenService.set_access_token_to_cookie(response, token_pair.access_token)
            TokenService.set_refresh_token_to_cookie(response, token_pair.refresh_token)
            TokenService.set_csrf_token_to_cookie(response, token_pair.csrf_token)

            return response

    except ValueError as e:
        logger.warning("OIDC state validation failed: %s", str(e))
        return {"error": "Authentication failed"}, 400
    except Exception as e:
        logger.exception("OIDC callback error")
        return {"error": "Authentication failed"}, 400


@api.post("/console/api/enterprise/sso/oidc/exchange-token")
def oidc_exchange_token():
    """Exchange short-lived SSO code for actual access token.
    This replaces passing tokens directly in URL query parameters.
    """
    data = request.get_json(silent=True) or {}
    short_code = data.get("code", "")

    if not short_code:
        return {"error": "Missing code"}, 400

    code_key = f"webapp_sso_code:{short_code}"
    token = redis_client.get(code_key)

    if not token:
        return {"error": "Invalid or expired code"}, 400

    # Delete immediately to prevent replay
    redis_client.delete(code_key)

    return {"access_token": token.decode()}


@api.get("/console/api/logout")
def sso_logout():
    """Logout from Dify and redirect to Authelia logout to clear SSO session."""
    # Build Authelia logout URL from OIDC discovery URL
    # e.g. https://dify.example.com/auth/.well-known/openid-configuration → https://dify.example.com/auth/#/logout
    discovery_url = config.OIDC_DISCOVERY_URL
    authelia_base = discovery_url.split("/.well-known")[0] if "/.well-known" in discovery_url else ""
    authelia_logout_url = f"{authelia_base}/#/logout" if authelia_base else f"{config.CONSOLE_WEB_URL}/auth/#/logout"

    response = redirect(authelia_logout_url)

    # Clear Dify auth cookies
    is_secure = TokenService.is_secure()
    for cookie_name in ["access_token", "refresh_token", "csrf_token"]:
        real_name = TokenService.real_cookie_name(cookie_name)
        response.set_cookie(real_name, "", expires=0, path="/", httponly=True, secure=is_secure, samesite="Lax")

    return response


@api.get("/api/enterprise/sso/oidc/login")
@api.get("/api/enterprise/sso/members/oidc/login")
def oidc_login_callback():
    app_code = request.args.get("app_code", "")
    redirect_url = request.args.get("redirect_url", "")
    login_url, state = oidc_service.get_login_url(f"app_code={app_code}&redirect_url={redirect_url}")
    return {"url": login_url}
