"""
Обработка ошибок и исключения
"""

import logging
from typing import Optional
from fastapi import HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    """Формат error response"""
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
    timestamp: Optional[str] = None


class ValidationError(HTTPException):
    """Validation error"""
    def __init__(self, detail: str, code: str = "VALIDATION_ERROR"):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": detail, "code": code}
        )


class ResourceNotFoundError(HTTPException):
    """Resource not found"""
    def __init__(self, resource: str, resource_id: Optional[str] = None):
        detail = f"{resource} not found"
        if resource_id:
            detail += f": {resource_id}"
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": detail, "code": "NOT_FOUND"}
        )


class UnauthorizedError(HTTPException):
    """Unauthorized access"""
    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": detail, "code": "UNAUTHORIZED"},
            headers={"WWW-Authenticate": "Bearer"}
        )


class ForbiddenError(HTTPException):
    """Forbidden access"""
    def __init__(self, detail: str = "Access denied"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": detail, "code": "FORBIDDEN"}
        )


class ConflictError(HTTPException):
    """Resource conflict (e.g., duplicate)"""
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": detail, "code": "CONFLICT"}
        )


class BadRequestError(HTTPException):
    """Bad request"""
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": detail, "code": "BAD_REQUEST"}
        )


class RateLimitError(HTTPException):
    """Rate limit exceeded"""
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "Rate limit exceeded", "code": "RATE_LIMIT"}
        )


def handle_db_error(error: Exception) -> HTTPException:
    """Convert DB error to HTTP exception"""
    logger.error(f"Database error: {error}")
    
    error_str = str(error).lower()
    
    if "unique" in error_str:
        return ConflictError("Resource already exists")
    elif "foreign key" in error_str:
        return BadRequestError("Invalid foreign key reference")
    elif "not null" in error_str:
        return ValidationError("Required field is missing")
    else:
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Database error", "code": "DB_ERROR"}
        )


def log_error(error: Exception, context: Optional[str] = None):
    """Log error with context"""
    msg = f"Error"
    if context:
        msg += f" ({context})"
    msg += f": {error}"
    logger.error(msg)
