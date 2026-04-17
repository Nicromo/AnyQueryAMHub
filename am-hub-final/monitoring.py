"""
Monitoring and Metrics Endpoints
Prometheus-compatible metrics for monitoring
"""

import time
import logging
from datetime import datetime
from typing import Dict, Any
from prometheus_client import Counter, Histogram, Gauge, generate_latest

logger = logging.getLogger(__name__)

# Metrics
request_count = Counter(
    'amhub_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status']
)

request_duration = Histogram(
    'amhub_request_duration_seconds',
    'HTTP request duration',
    ['method', 'endpoint']
)

active_connections = Gauge(
    'amhub_websockets_active',
    'Active WebSocket connections'
)

database_queries = Counter(
    'amhub_database_queries_total',
    'Total database queries',
    ['operation', 'model']
)

sync_operations = Counter(
    'amhub_sync_operations_total',
    'Total sync operations',
    ['source', 'status']
)

api_errors = Counter(
    'amhub_api_errors_total',
    'Total API errors',
    ['error_type', 'endpoint']
)


class MetricsCollector:
    """Collect and expose metrics"""
    
    def __init__(self):
        self.start_time = datetime.now()
        self.request_count_internal = 0
        self.error_count = 0
        self.sync_operations_count = 0
    
    def get_health_metrics(self) -> Dict[str, Any]:
        """Get health metrics"""
        uptime = (datetime.now() - self.start_time).total_seconds()
        
        return {
            "status": "healthy",
            "uptime_seconds": uptime,
            "requests": self.request_count_internal,
            "errors": self.error_count,
            "sync_operations": self.sync_operations_count,
            "timestamp": datetime.now().isoformat(),
        }
    
    def record_request(self, method: str, endpoint: str, status: int, seconds: float = None):
        """Record HTTP request"""
        self.request_count_internal += 1
        request_count.labels(method=method, endpoint=endpoint, status=status).inc()
        
        if seconds:
            request_duration.labels(method=method, endpoint=endpoint).observe(seconds)
    
    def record_error(self, error_type: str, endpoint: str):
        """Record API error"""
        self.error_count += 1
        api_errors.labels(error_type=error_type, endpoint=endpoint).inc()
    
    def record_db_query(self, operation: str, model: str):
        """Record database query"""
        database_queries.labels(operation=operation, model=model).inc()
    
    def record_sync(self, source: str, status: str):
        """Record sync operation"""
        self.sync_operations_count += 1
        sync_operations.labels(source=source, status=status).inc()
    
    def update_websocket_connections(self, count: int):
        """Update active WebSocket connections"""
        active_connections.set(count)
    
    def get_prometheus_metrics(self) -> str:
        """Get metrics in Prometheus format"""
        return generate_latest().decode('utf-8')


# Global metrics collector
metrics = MetricsCollector()


def get_startup_checks() -> Dict[str, Any]:
    """Get startup health checks"""
    try:
        from database import SessionLocal
        from models import User, Client
        
        with SessionLocal() as db:
            user_count = db.query(User).count()
            client_count = db.query(Client).count()
        
        return {
            "database": "ok",
            "users": user_count,
            "clients": client_count,
            "timestamp": datetime.now().isoformat(),
        }
    
    except Exception as e:
        logger.error(f"Startup check error: {e}")
        return {
            "database": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


def get_integration_status() -> Dict[str, Any]:
    """Get integration status"""
    from config import settings
    
    cfg = settings()
    
    return {
        "airtable": bool(cfg.AIRTABLE_API_KEY),
        "merchrules": bool(cfg.MERCHRULES_API_KEY),
        "ktalk": bool(cfg.KTALK_API_TOKEN),
        "tbank_time": bool(cfg.TIME_API_TOKEN),
        "dashboard": bool(cfg.DASHBOARD_API_KEY),
        "email": bool(cfg.EMAIL_PROVIDER),
        "groq_ai": bool(cfg.GROQ_API_KEY),
        "telegram": bool(cfg.TELEGRAM_BOT_TOKEN),
        "timestamp": datetime.now().isoformat(),
    }


# Request timing middleware (to be added to FastAPI)
class TimingMiddleware:
    """Track request timing"""
    
    async def __call__(self, request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Record metrics
        metrics.record_request(
            request.method,
            request.url.path,
            response.status_code,
            process_time
        )
        
        response.headers["X-Process-Time"] = str(process_time)
        return response
