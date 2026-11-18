"""Pure-ASGI middleware: request IDs, access log, HTTP metrics.

Written as raw ASGI (not BaseHTTPMiddleware) so it never buffers or interferes with the SSE
stream returned by /chat.
"""
import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics
from app.core.logging import new_request_id, request_id_var

logger = logging.getLogger("app.access")
_SKIP_LOG = {"/health", "/metrics"}  # probes and scrapes would drown the log


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, metrics_enabled: bool = True):
        self.app = app
        self.metrics_enabled = metrics_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), None)
        request_id = new_request_id(incoming)
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status_code = 500  # if the app raises before responding, that is what the client gets

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        if self.metrics_enabled:
            metrics.HTTP_IN_FLIGHT.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            # Starlette stores the matched route in the scope; unmatched paths (404 scans) all map to
            # one label so attackers can't blow up metric cardinality.
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            if self.metrics_enabled:
                metrics.HTTP_IN_FLIGHT.dec()
                metrics.HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
                metrics.HTTP_DURATION.labels(method, route).observe(duration)
            if route not in _SKIP_LOG:
                logger.info(
                    "request",
                    extra={"method": method, "route": route, "status": status_code,
                           "duration_ms": round(duration * 1000, 1)},
                )
            request_id_var.reset(token)
