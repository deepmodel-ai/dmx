"""HTTP bearer token authentication middleware for the dmx MCP server.

Enabled when both ``REQUIRE_API_KEY=true`` and ``MCP_API_KEY`` are set in the
environment.  Only applies to the HTTP/SSE transport — stdio is unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from starlette.requests import Request
    from starlette.types import ASGIApp

__all__ = ["BearerTokenMiddleware"]


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Starlette ASGI middleware that enforces a static bearer token.

    Requests without a matching ``Authorization: Bearer <token>`` header
    receive a ``401 Unauthorized`` JSON response.

    Args:
        app: The downstream ASGI application.
        api_key: The expected bearer token value.
    """

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Coroutine[Any, Any, Response]],  # type: ignore[override]
    ) -> Response:
        auth = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if not auth.startswith(prefix) or auth[len(prefix) :] != self._api_key:
            return JSONResponse(
                {"error": "Unauthorized", "detail": "Valid Bearer token required."},
                status_code=401,
            )
        return await call_next(request)
