import base64
import hashlib
import logging
import re
import secrets
from datetime import timedelta
from typing import Dict
from urllib.parse import urlencode, unquote

import requests

from app.configs import config
from app.extensions.ext_database import db
from app.extensions.ext_redis import redis_client
from app.libs.helper import naive_utc_now
from app.models.account import Account, AccountStatus, TenantAccountJoin, TenantAccountRole
from app.models.model import Site
from app.services.passport import PassportService
from app.services.token import TokenService

logger = logging.getLogger(__name__)

# Email validation pattern (RFC 5322 simplified)
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
MAX_EMAIL_LENGTH = 254
MAX_NAME_LENGTH = 255


class OIDCService:
    def __init__(self):
        self.client_id = config.OIDC_CLIENT_ID
        self.client_secret = config.OIDC_CLIENT_SECRET
        self.discovery_url = config.OIDC_DISCOVERY_URL
        self.redirect_uri = config.OIDC_REDIRECT_URI
        self.scope = config.OIDC_SCOPE
        self.response_type = config.OIDC_RESPONSE_TYPE
        self.tenant_id = config.TENANT_ID
        self.account_default_role = config.ACCOUNT_DEFAULT_ROLE
        self.passport_service = PassportService()
        self.token_service = TokenService()

        # Internal URL for pod-to-pod discovery fetch (falls back to external URL)
        self._internal_discovery_url = config.OIDC_INTERNAL_DISCOVERY_URL or self.discovery_url

        # Load OIDC configuration
        self._load_oidc_config()

    def _load_oidc_config(self, retries: int = 5, backoff: float = 3.0):
        """Load OIDC provider configuration from discovery URL.

        Retries with linear backoff to handle cases where the OIDC provider
        is not yet reachable at startup (e.g. pod scheduling order).
        """
        import time

        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                response = requests.get(self._internal_discovery_url, timeout=10)
                if response.status_code == 200:
                    oidc_config = response.json()
                    self.authorization_endpoint = oidc_config.get('authorization_endpoint')
                    self.token_endpoint = oidc_config.get('token_endpoint')
                    self.userinfo_endpoint = oidc_config.get('userinfo_endpoint')
                    logger.info("OIDC configuration loaded successfully")
                    return
                last_error = Exception(f"HTTP {response.status_code}: {response.text}")
                logger.warning("OIDC config load failed (attempt %d/%d): %s", attempt, retries, last_error)
            except Exception as e:
                last_error = e
                logger.warning("OIDC config load error (attempt %d/%d): %s", attempt, retries, e)

            if attempt < retries:
                time.sleep(backoff * attempt)

        logger.error("Failed to load OIDC configuration after %d attempts: %s", retries, last_error)
        raise Exception(f"Failed to load OIDC configuration: {last_error}")

    def check_oidc_config(self) -> bool:
        """Checks if the OIDC configuration is complete."""
        if not self.authorization_endpoint or not self.token_endpoint or not self.userinfo_endpoint:
            return False
        return True

    @staticmethod
    def _generate_pkce_pair() -> tuple[str, str]:
        """Generate PKCE code_verifier and code_challenge (S256)."""
        code_verifier = secrets.token_urlsafe(48)  # 64 chars
        digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
        return code_verifier, code_challenge

    @staticmethod
    def _generate_state() -> str:
        """Generate cryptographically random state for CSRF protection."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def _generate_nonce() -> str:
        """Generate cryptographically random nonce for ID token replay protection."""
        return secrets.token_urlsafe(32)

    def get_login_url(self, redirect_uri_params: str = "") -> tuple[str, str]:
        """Generate OIDC login URL with CSRF state, PKCE, and nonce.

        Returns:
            tuple of (login_url, state) - state must be stored server-side for validation
        """
        state = self._generate_state()
        nonce = self._generate_nonce()
        code_verifier, code_challenge = self._generate_pkce_pair()

        # Store state, nonce, and PKCE verifier in Redis (5 min TTL)
        state_key = f"oidc_state:{state}"
        redis_client.setex(state_key, timedelta(minutes=5), f"{nonce}:{code_verifier}")

        params = {
            'client_id': self.client_id,
            'response_type': self.response_type,
            'scope': self.scope,
            'redirect_uri': self.redirect_uri,
            'state': state,
            'nonce': nonce,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }

        if redirect_uri_params:
            params['redirect_uri'] = self.redirect_uri + "?" + unquote(redirect_uri_params)

        return f"{self.authorization_endpoint}?{urlencode(params)}", state

    def validate_state(self, state: str) -> tuple[str, str]:
        """Validate OAuth state and return (nonce, code_verifier). Raises on invalid/expired state."""
        if not state:
            raise ValueError("Missing OAuth state parameter")

        state_key = f"oidc_state:{state}"
        stored = redis_client.get(state_key)
        if not stored:
            raise ValueError("Invalid or expired OAuth state")

        # Delete state immediately to prevent replay
        redis_client.delete(state_key)

        nonce, code_verifier = stored.decode().split(':', 1)
        return nonce, code_verifier

    def get_token(self, code: str, code_verifier: str, redirect_uri_params: str = "") -> Dict:
        """Exchange authorization code for tokens, with PKCE verification."""
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.redirect_uri,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code_verifier': code_verifier,
        }

        if redirect_uri_params:
            data['redirect_uri'] = self.redirect_uri + "?" + unquote(redirect_uri_params)

        response = requests.post(self.token_endpoint, data=data)
        if response.status_code != 200:
            logger.error("Failed to get token: status_code=%d", response.status_code)
            raise Exception("Failed to get token")
        return response.json()

    def get_user_info(self, access_token: str) -> Dict:
        """Retrieve user info from OIDC provider."""
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get(self.userinfo_endpoint, headers=headers)
        if response.status_code != 200:
            logger.error("Failed to get user info: status_code=%d", response.status_code)
            raise Exception("Failed to get user info")
        return response.json()

    def bind_account(self, code: str, client_host: str, code_verifier: str = "", redirect_uri_params: str = "") -> Account:
        """Bind an OIDC user to the system (create or update account)."""
        try:
            # Exchange authorization code for access token
            token_response = self.get_token(code, code_verifier, redirect_uri_params)
            access_token = token_response.get('access_token')

            # Get user info from OIDC provider
            user_info = self.get_user_info(access_token)
            user_name = user_info.get('name', '')
            user_email = user_info.get('email', '')
            user_roles = user_info.get('roles', [])
            logger.debug("User info retrieved for email: %s", user_email)

            # Validate email
            if not user_email:
                raise Exception("User email is required")
            user_email = user_email.strip().lower()
            if len(user_email) > MAX_EMAIL_LENGTH or not EMAIL_REGEX.match(user_email):
                raise Exception("Invalid email format")

            # Validate and sanitize name
            if not user_name:
                user_name = user_email.split('@')[0]
            user_name = user_name.strip()[:MAX_NAME_LENGTH]

            # Determine user role (priority: admin > editor > normal > default)
            user_role = TenantAccountRole(self.account_default_role) if TenantAccountRole.is_valid_role(
                self.account_default_role) else TenantAccountRole.NORMAL
            if TenantAccountRole.ADMIN in user_roles:
                user_role = TenantAccountRole.ADMIN
            elif TenantAccountRole.EDITOR in user_roles:
                user_role = TenantAccountRole.EDITOR
            elif TenantAccountRole.NORMAL in user_roles:
                user_role = TenantAccountRole.NORMAL

            # Look up existing account
            account = Account.get_by_email(user_email)

            # Create account if not found
            if not account:
                logger.info("Creating user: %s, role: %s", user_email, user_role)
                account = Account.create(
                    email=user_email,
                    name=user_name,
                    avatar="",
                )
                TenantAccountJoin.create(self.tenant_id, account.id, user_role)
            else:
                # If user exists, check tenant membership
                tenant_account_join = TenantAccountJoin.get_by_account(
                    self.tenant_id, account.id
                )
                if not tenant_account_join:
                    logger.info("User %s not in current tenant, creating join: role %s", user_email, user_role)
                    tenant_account_join = TenantAccountJoin.create(self.tenant_id, account.id, user_role)
                else:
                    # Update role if changed, but never downgrade owner
                    if tenant_account_join.role == TenantAccountRole.OWNER:
                        logger.debug("Skipping role update for owner: %s", user_email)
                    elif not user_roles:
                        # OIDC provider returned no roles — preserve role set in Dify UI
                        logger.debug("No roles in OIDC response, keeping existing role for: %s", user_email)
                    elif tenant_account_join.role != user_role:
                        logger.info("User role updated: %s (%s -> %s)", user_email, tenant_account_join.role, user_role)
                        tenant_account_join.role = user_role
                        db.session.add(tenant_account_join)

            # Update login info
            account.last_login_at = naive_utc_now()
            account.last_login_ip = client_host
            if account.status != AccountStatus.ACTIVE:
                account.status = AccountStatus.ACTIVE
            if account.name != user_name:
                account.name = user_name

            db.session.add(account)
            db.session.commit()

            effective_join = TenantAccountJoin.get_by_account(self.tenant_id, account.id)
            effective_role = effective_join.role if effective_join else user_role
            logger.info("User authenticated successfully: %s, role: %s", user_email, effective_role)
            return account
        except Exception as e:
            logger.exception("Error during user authentication: %s", str(e))
            raise

    def handle_callback(self, code: str, client_host: str, code_verifier: str = "",
                        redirect_uri_params: str = "", app_code: str = "") -> Dict[str, str]:
        """Handle OIDC callback, return access token and refresh token."""
        try:
            account = self.bind_account(code, client_host, code_verifier, redirect_uri_params)

            # Generate JWT token
            exp_dt = naive_utc_now() + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
            exp = int(exp_dt.timestamp())
            account_id = str(account.id)

            if redirect_uri_params:
                auth_type = "internal"
                logger.debug("Processing webapp login, app_code=%s", app_code)

                site = db.session.query(Site).filter(Site.code == app_code).first()
                if site:
                    access_mode = redis_client.get(f"webapp_access_mode:{site.app_id}")
                    if access_mode:
                        if access_mode.decode() == "public":
                            auth_type = "public"
                        if access_mode.decode() == "sso_verified":
                            auth_type = "external"
                        logger.debug("Webapp login type: %s => %s", access_mode.decode(), auth_type)

                # Webapp login payload
                payload = {
                    "user_id": account_id,
                    "end_user_id": account_id,
                    "session_id": account.email,
                    "auth_type": auth_type,
                    "token_source": "webapp_login_token",
                    "exp": exp,
                    "sub": "Web API Passport",
                }

                access_token = self.passport_service.issue(payload)

                return {
                    "access_token": access_token,
                }

            else:
                payload = {
                    "user_id": account_id,
                    "exp": exp,
                    "iss": config.EDITION,
                    "sub": "Console API Passport",
                }

                # Generate access token
                console_access_token: str = self.passport_service.issue(payload)

                # Generate and store refresh token
                refresh_token = self.token_service.generate_refresh_token()
                self.token_service.store_refresh_token(refresh_token, account_id)

                return {
                    "access_token": console_access_token,
                    "refresh_token": refresh_token,
                }

        except Exception as e:
            logger.exception("Error during OIDC callback: %s", str(e))
            raise
