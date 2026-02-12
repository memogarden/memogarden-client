"""Authentication manager for MemoGarden client.

Handles API key authentication and token management.
"""

from typing import Literal


class AuthManager:
    """Manages authentication credentials for MemoGarden API.

    Supports API key authentication (for agents and programmatic access).
    JWT token support can be added in the future.

    Attributes:
        auth_type: Type of authentication being used
        api_key: API key string (if auth_type is "api_key")

    Example:
        >>> auth = AuthManager(api_key="mg_sk_agent_abc123...")
        >>> headers = auth.get_headers()
        >>> headers["Authorization"]
        'Bearer mg_sk_agent_abc123...'
    """

    def __init__(
        self,
        api_key: str | None = None,
        auth_type: Literal["api_key"] = "api_key",
    ):
        """Initialize authentication manager.

        Args:
            api_key: MemoGarden API key (starts with "mg_sk_")
            auth_type: Type of authentication (only "api_key" supported)

        Raises:
            ValueError: If auth_type is not supported or credentials are missing
        """
        self.auth_type = auth_type

        if auth_type == "api_key":
            if not api_key:
                raise ValueError("api_key is required for api_key authentication")
            if not api_key.startswith("mg_sk_"):
                raise ValueError("Invalid API key format: must start with 'mg_sk_'")
            self.api_key = api_key
        else:
            raise ValueError(f"Unsupported auth_type: {auth_type}")

    def get_headers(self) -> dict[str, str]:
        """Get authorization headers for API requests.

        Returns:
            Dict with Authorization header

        Example:
            >>> auth = AuthManager(api_key="mg_sk_agent_abc123...")
            >>> auth.get_headers()
            {'Authorization': 'Bearer mg_sk_agent_abc123...'}
        """
        if self.auth_type == "api_key":
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def get_auth_string(self) -> str:
        """Get authentication string for display/logging.

        Returns:
            Safe string representing the authentication (partial key)

        Example:
            >>> auth = AuthManager(api_key="mg_sk_agent_abc123...")
            >>> auth.get_auth_string()
            'api_key: mg_sk_agent_...'
        """
        if self.auth_type == "api_key":
            # Show first 12 characters (prefix only)
            prefix = self.api_key[:12]
            return f"api_key: {prefix}..."
        return f"{self.auth_type}: ***"

    def __repr__(self) -> str:
        return f"AuthManager({self.get_auth_string()})"
