"""Domain exception classes for the service layer.

Each exception maps to an HTTP status code at the router layer.
Services raise these; the factory exception handlers translate to HTTP responses.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain exceptions."""


class NotFoundError(DomainError):
    """Raised when a requested entity does not exist (-> 404)."""


class ConflictError(DomainError):
    """Raised on duplicate or stale-state conflicts (-> 409)."""


class ForbiddenError(DomainError):
    """Raised when the caller lacks required permissions (-> 403)."""


class ValidationError(DomainError):
    """Raised when input fails domain-level validation (-> 422)."""


class ExternalServiceError(DomainError):
    """Raised when an external dependency fails (-> 502/503)."""
