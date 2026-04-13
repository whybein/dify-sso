from pydantic import Field
from pydantic_settings import BaseSettings


class SSOConfig(BaseSettings):
    OIDC_CLIENT_ID: str = Field(
        description="Client ID for the OpenID Connect provider",
        default="",
    )

    OIDC_CLIENT_SECRET: str = Field(
        description="Client secret for the OpenID Connect provider",
        default="",
    )

    OIDC_DISCOVERY_URL: str = Field(
        description="Discovery URL for the OpenID Connect provider (external, used for browser redirects)",
        default="",
    )

    OIDC_INTERNAL_DISCOVERY_URL: str = Field(
        description=(
            "Internal discovery URL for fetching OIDC config at startup (K8s service URL). "
            "Falls back to OIDC_DISCOVERY_URL if not set."
        ),
        default="",
    )

    OIDC_REDIRECT_URI: str = Field(
        description="Redirect URI for the OpenID Connect provider",
        default="",
    )

    OIDC_SCOPE: str = Field(
        description="Scope for the OpenID Connect provider",
        default="openid profile email roles",
    )

    OIDC_RESPONSE_TYPE: str = Field(
        description="Response type for the OpenID Connect provider",
        default="code",
    )
