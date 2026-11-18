"""Authentication manager for MemoGarden client.

Handles both transports the server distinguishes (RFC-001/RFC-010):
- API key authentication (agents and programmatic access) via X-API-Key
- Operator JWT sessions (RFC-015 §5.1) via Authorization: Bearer —
  the only transport that reaches the governed endpoints (identity,
  grants, scope minting, scope-policy)

An mg_sk_ key in the Bearer header is rejected by the server by design:
Bearer is JWT-only, API keys are X-API-Key-only.
"""

from typing import Literal


class AuthManager:
    """Manages authentication credentials for MemoGarden API.

    Supports API key authentication (for agents and programmatic access)
    and operator JWT sessions (for the operator-governed surface). When
    both are supplied, auth_type picks explicitly; jwt alone implies
    "jwt", api_key alone implies "api_key".

    Attributes:
        auth_type: Type of authentication being used ("api_key" | "jwt")
        api_key: API key string (if auth_type is "api_key")
        jwt: Operator JWT (if auth_type is "jwt")

    Example:
        >>> auth = AuthManager(api_key="mg_sk_agent_abc123...")
        >>> headers = auth.get_headers()
        >>> headers["X-API-Key"]
        'mg_sk_agent_abc123...'
        >>> auth = AuthManager(jwt="eyJ...")
        >>> auth.get_headers()["Authorization"]
        'Bearer eyJ...'
    """

    def __init__(
        self,
        api_key: str | None = None,
        jwt: str | None = None,
        auth_type: Literal["api_key", "jwt"] | None = None,
    ):
        """Initialize authentication manager.

        Args:
            api_key: MemoGarden API key (starts with "mg_sk_")
            jwt: Operator JWT access token
            auth_type: Transport to use; inferred from the credential
                given when omitted (jwt wins if both are passed)

        Raises:
            ValueError: If auth_type is not supported or credentials are missing
        """
        if auth_type is None:
            auth_type = "jwt" if jwt is not None else "api_key"

        self.auth_type = auth_type

        if auth_type == "jwt":
            if not jwt:
                raise ValueError("jwt is required for jwt authentication")
            self.jwt = jwt
            self.api_key = None
        elif auth_type == "api_key":
            if not api_key:
                raise ValueError("api_key is required for api_key authentication")
            if not api_key.startswith("mg_sk_"):
                raise ValueError("Invalid API key format: must start with 'mg_sk_'")
            self.api_key = api_key
            self.jwt = None
        else:
            raise ValueError(f"Unsupported auth_type: {auth_type}")

    def get_headers(self) -> dict[str, str]:
        """Get authorization headers for API requests.

        API keys (mg_sk_*) are sent via the X-API-Key header; operator
        JWTs via Authorization: Bearer. The server validates Bearer
        tokens as JWT only and never falls through to its API-key check,
        so an mg_sk_ key in the Bearer header is rejected as an invalid
        JWT — the two transports never blur (RFC-001 transport alignment).

        Returns:
            Dict with the transport's auth header

        Example:
            >>> AuthManager(api_key="mg_sk_agent_abc123...").get_headers()
            {'X-API-Key': 'mg_sk_agent_abc123...'}
            >>> AuthManager(jwt="eyJ...").get_headers()
            {'Authorization': 'Bearer eyJ...'}
        """
        if self.auth_type == "jwt":
            return {"Authorization": f"Bearer {self.jwt}"}
        if self.auth_type == "api_key":
            return {"X-API-Key": self.api_key}
        return {}

    def get_auth_string(self) -> str:
        """Get authentication string for display/logging.

        Returns:
            Safe string representing the authentication (partial credential)

        Example:
            >>> AuthManager(api_key="mg_sk_agent_abc123...").get_auth_string()
            'api_key: mg_sk_agent_...'
        """
        if self.auth_type == "api_key":
            # Show first 12 characters (prefix only)
            prefix = self.api_key[:12]
            return f"api_key: {prefix}..."
        return "jwt: ***"

    def __repr__(self) -> str:
        return f"AuthManager({self.get_auth_string()})"
