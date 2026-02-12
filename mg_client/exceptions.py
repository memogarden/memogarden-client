"""Exception classes for MemoGarden client.

All client exceptions inherit from MemoGardenClientError for easy catching.
"""


class MemoGardenClientError(Exception):
    """Base exception for all MemoGarden client errors.

    Attributes:
        message: Human-readable error description
        details: Optional dict with additional error context
        type: Error type name from server response
    """

    def __init__(self, message: str, details: dict | None = None, type: str | None = None):
        self.message = message
        self.details = details or {}
        self.type = type or self.__class__.__name__
        super().__init__(self.message)

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


class AuthenticationError(MemoGardenClientError):
    """Authentication failed (invalid API key, token expired, etc.)."""


class ResourceNotFoundError(MemoGardenClientError):
    """Requested resource not found (404)."""


class ValidationError(MemoGardenClientError):
    """Request validation failed (400)."""


class ConflictError(MemoGardenClientError):
    """Optimistic locking conflict (409)."""


class NetworkError(MemoGardenClientError):
    """Network or connection error."""


class RateLimitError(MemoGardenClientError):
    """Rate limit exceeded (429)."""


class InternalServerError(MemoGardenClientError):
    """Server-side error (500)."""


def error_from_response(response_data: dict) -> MemoGardenClientError:
    """Create appropriate exception from server error response.

    Args:
        response_data: Error response dict with 'error' key

    Returns:
        Appropriate MemoGardenClientError subclass instance

    Example:
        >>> error = error_from_response({
        ...     "error": {"type": "ResourceNotFound", "message": "Not found"}
        ... })
        >>> isinstance(error, ResourceNotFoundError)
        True
    """
    error_info = response_data.get("error", {})
    error_type = error_info.get("type", "Unknown")
    message = error_info.get("message", "Unknown error")
    details = error_info.get("details")

    # Map server error types to client exception classes
    error_classes = {
        "AuthenticationError": AuthenticationError,
        "ResourceNotFound": ResourceNotFoundError,
        "ValidationError": ValidationError,
        "ConflictError": ConflictError,
        "RateLimitError": RateLimitError,
        "InternalServerError": InternalServerError,
    }

    exception_class = error_classes.get(error_type, MemoGardenClientError)
    return exception_class(message, details=details, type=error_type)
