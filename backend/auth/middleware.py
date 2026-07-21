"""
SecurityMiddleware — the public-exposure gate. Two layers:

  1. IP whitelist (ALLOWED_IPS). Trusts X-Forwarded-For from reverse proxies.
     Set ALLOWED_IPS="*" for an open/public deployment.
  2. Auth on PROTECTED routes only. Public read-only API + dashboard stay open so a
     public site can display forecasts, but anything that spends money or changes
     state requires admin auth.

PROTECTED (admin JWT, or X-Cron-Secret, or DEV_MODE):
  • /admin-secure-panel/  + /api/admin/        — config / weights / feedback
  • /api/cycle                                  — triggers an AI cycle (burns tokens, may trade)
  • /api/briefing/generate                      — generates + Telegram-sends a brief (burns tokens)

PUBLIC (IP-whitelist only): the dashboard and read endpoints
(/api/forecast, /api/signal, /api/agents, /api/accuracy, /api/news, /api/hierarchy,
 /api/briefing/latest, /api/activity, /health, /docs).
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from backend.config import settings
from backend.auth.jwt_handler import verify_token

PROTECTED_PREFIXES = (
    "/admin-secure-panel/", "/api/admin/", "/api/cycle", "/api/briefing/generate",
    "/api/backtest",   # POST burns the owner's FRED quota + mutates shared cache → gate it
)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # ── Layer 1: IP whitelist (applies to everything unless "*") ──
        if settings.ALLOWED_IPS.strip() != "*":
            forwarded = request.headers.get("x-forwarded-for", "")
            client_ip = forwarded.split(",")[0].strip() if forwarded else (
                request.client.host if request.client else "unknown")
            if client_ip not in settings.allowed_ip_list:
                return JSONResponse({"detail": "Forbidden (IP not whitelisted)"}, status_code=403)

        path = request.url.path
        if not any(path.startswith(p) for p in PROTECTED_PREFIXES):
            return await call_next(request)   # public read surface

        # ── Layer 2: protected route → require admin ──
        if settings.DEV_MODE:
            request.state.role = "admin"
            return await call_next(request)
        # External scheduler / webhook bypass via shared secret (no interactive login).
        cron = request.headers.get("x-cron-secret", "")
        if settings.CRON_SECRET and cron and cron == settings.CRON_SECRET:
            request.state.role = "admin"
            return await call_next(request)
        # Admin JWT (cookie or Bearer).
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip() or request.cookies.get("access_token", "")
        payload = verify_token(token)
        if not payload or payload.get("role") != "admin":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        request.state.role = "admin"
        request.state.user = payload.get("sub", "owner")
        return await call_next(request)
