from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to THIS file (project root), not the current working
# directory. The daily-brief scheduled task runs with cwd=project root, but any
# other entrypoint (or a cwd-less invocation) would otherwise silently fail to
# load the Telegram token and degrade brief delivery to a no-op. Absolute path
# makes loading cwd-independent; if the file is absent (e.g. cloud deploy with
# env vars injected) pydantic-settings just ignores it.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # ── Core ──
    ANTHROPIC_API_KEY: str = ""
    MODEL_ID: str = "claude-sonnet-4-6"

    # ── Data sources ──
    FRED_API_KEY: str = ""
    BOK_ECOS_KEY: str = ""
    NEWS_RETENTION_DAYS: int = 14       # keep N days of dated news archive; older is pruned

    # ── Trading bridge ──
    BROKER: str = "paper"               # "paper" | "kis"
    KIS_APP_KEY: str = ""
    KIS_APP_SECRET: str = ""
    KIS_ACCOUNT_NO: str = ""
    KIS_PAPER: bool = True
    MAX_TRADE_NOTIONAL_USD: float = 10000.0      # per-trade cap
    ENABLE_LIVE_TRADING: bool = False

    # ── Risk manager (portfolio-level) ──
    MAX_TOTAL_NOTIONAL_USD: float = 30000.0      # max aggregate open exposure
    DAILY_LOSS_LIMIT_KRW: float = 2_000_000.0    # halt new trades past this daily loss

    # ── Telegram daily brief delivery ──
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""          # CSV of chat ids to deliver the brief to
    BRIEFING_HOUR_KST: int = 8          # daily brief generation hour (KST)

    # ── Security (institutional-grade, ported from Fed-Watcher) ──
    JWT_SECRET: str = ""                # ≥32 chars in production
    ADMIN_PASSWORD_HASH: str = ""       # bcrypt hash (run setup.py)
    OWNER_MAC: str = ""                 # hardware lock; empty = unlocked
    ALLOWED_IPS: str = "127.0.0.1"      # CSV; "*" = open (cloud/public)
    DATABASE_URL: str = "sqlite+aiosqlite:///./krw_watcher.db"

    # ── Deployment ──
    # true → run DATA-ONLY (free sweeps), no token-burning AI cycles/brief.
    # Use for a public read-only mirror, or to control cost.
    DISABLE_AUTO_CYCLE: bool = False
    # Daily report guarantee: generate exactly ONE report (AI cycle + brief) per KST day,
    # even when DISABLE_AUTO_CYCLE turns off the every-2h cycles. The 30-min data sweep
    # self-heals a missed day. Bounded cost = one report/day. Set true to opt out.
    DISABLE_DAILY_REPORT: bool = False
    DAILY_REPORT_HOUR_KST: int = 8      # earliest KST hour the daily report may generate
    # If set, an external scheduler/webhook may trigger /api/cycle and
    # /api/briefing/generate by sending header `X-Cron-Secret: <value>` (no login).
    CRON_SECRET: str = ""

    # ── Dev ──
    DEV_MODE: bool = True               # skip MAC + JWT (IP whitelist still applies)
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3010"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_ip_list(self) -> list[str]:
        return [ip.strip() for ip in self.ALLOWED_IPS.split(",") if ip.strip()]

    @property
    def jwt_ready(self) -> bool:
        return len(self.JWT_SECRET) >= 32


settings = Settings()
