"""
Middleware для обработки запросов, логирования, rate limiting и т.д.
"""

import logging
import time
from typing import Callable
from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware для логирования всех запросов"""

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        # Skip logging for health checks
        if request.url.path in ["/health", "/docs", "/openapi.json"]:
            return await call_next(request)

        start_time = time.time()
        
        # Log request
        logger.info(
            f"→ {request.method} {request.url.path} "
            f"| ip: {request.client.host} "
            f"| user: {request.headers.get('X-User-ID', 'anonymous')}"
        )

        try:
            response = await call_next(request)
            
            # Log response
            process_time = time.time() - start_time
            logger.info(
                f"← {request.method} {request.url.path} "
                f"| status: {response.status_code} "
                f"| time: {process_time:.3f}s"
            )
            
            # Add processing time header
            response.headers["X-Process-Time"] = str(process_time)
            
            return response
            
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"✗ {request.method} {request.url.path} "
                f"| error: {e} "
                f"| time: {process_time:.3f}s"
            )
            raise


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware для rate limiting"""

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests = defaultdict(list)  # ip -> [(timestamp, ...)]

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        # Skip rate limiting for health checks
        if request.url.path in ["/health"]:
            return await call_next(request)

        client_ip = request.client.host
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)

        # Clean old requests
        self.requests[client_ip] = [
            ts for ts in self.requests[client_ip]
            if ts > minute_ago
        ]

        # Check limit
        if len(self.requests[client_ip]) >= self.requests_per_minute:
            logger.warning(f"🚫 Rate limit exceeded for {client_ip}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"error": "Rate limit exceeded"}
            )

        # Add request
        self.requests[client_ip].append(now)

        return await call_next(request)


class CORSMiddleware(BaseHTTPMiddleware):
    """Middleware для CORS"""

    def __init__(self, app, allowed_origins: list = None):
        super().__init__(app)
        self.allowed_origins = allowed_origins or ["*"]

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        # Handle preflight
        if request.method == "OPTIONS":
            return JSONResponse(
                content={},
                headers={
                    "Access-Control-Allow-Origin": ",".join(self.allowed_origins),
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                }
            )

        response = await call_next(request)
        
        # Add CORS headers
        response.headers["Access-Control-Allow-Origin"] = ",".join(self.allowed_origins)
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        
        return response


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware для обработки необработанных ошибок"""

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        try:
            return await call_next(request)
        except HTTPException:
            # Let FastAPI handle HTTP exceptions
            raise
        except Exception as e:
            logger.exception(f"Unhandled error in {request.method} {request.url.path}: {e}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": "Internal server error",
                    "detail": str(e) if str(e) else "Unknown error"
                }
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware для добавления security headers и cache-control для статики."""

    # Cache-Control TTLs for static assets (seconds)
    _STATIC_CACHE: dict = {
        ".js":    86400,   # 1 day  — bundle.js, vendor chunks
        ".css":   3600,    # 1 hour — tokens.css, stylesheets
        ".woff":  604800,  # 7 days — fonts
        ".woff2": 604800,
        ".ttf":   604800,
        ".png":   86400,
        ".svg":   86400,
        ".ico":   86400,
    }

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        response = await call_next(request)

        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Add cache-control for static assets
        path = request.url.path
        if path.startswith("/static/") and response.status_code == 200:
            ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
            ttl = self._STATIC_CACHE.get(ext)
            if ttl:
                response.headers["Cache-Control"] = f"public, max-age={ttl}"

        return response
