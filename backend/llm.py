"""LLM client selector — API key or Max/Pro subscription.

The app makes plain single-shot Messages-style calls (system + one user message →
text). `make_client()` returns either:

  * settings.LLM_BACKEND == "api"          → anthropic.AsyncAnthropic (x-api-key,
                                             pay-as-you-go credits), or
  * settings.LLM_BACKEND == "subscription" → a drop-in that routes each call through
                                             the Claude Agent SDK (Claude Code CLI)
                                             authenticated by a Max/Pro subscription
                                             (CLAUDE_CODE_OAUTH_TOKEN) — no API credits.

The subscription client exposes just the `.messages.create(...)` surface the app uses
and returns a response whose `.content[0].text` holds the assistant text, so call sites
stay unchanged. cache_control / thinking / temperature kwargs are accepted and ignored
(the Agent SDK doesn't expose them); the JSON the models return is parsed as before.
"""
import asyncio

from backend.config import settings

# Full API model id → Claude Code CLI alias.
_MODEL_ALIAS = {
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-8": "opus",
    "claude-haiku-4-5-20251001": "haiku",
    "claude-haiku-4-5": "haiku",
}

_semaphore: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, settings.LLM_MAX_CONCURRENCY))
    return _semaphore


class _Block:
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Block(text)]


def _system_text(system) -> str | None:
    if not system:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, list):  # [{"type":"text","text":..., "cache_control":...}]
        return "\n\n".join(s.get("text", "") for s in system if isinstance(s, dict)) or None
    return str(system)


def _user_text(messages) -> str:
    parts = []
    for m in messages or []:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.append("".join(b.get("text", "") for b in c if isinstance(b, dict)))
    return "\n\n".join(parts)


async def _run_subscription(system: str | None, user: str, model: str | None) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, query

    opts = ClaudeAgentOptions(
        system_prompt=system,
        allowed_tools=[],          # no tools — a plain completion
        max_turns=1,
        setting_sources=[],        # ignore filesystem CLAUDE.md / project settings
        model=_MODEL_ALIAS.get(model or "", "sonnet"),
    )
    text = ""
    async with _sem():
        async for msg in query(prompt=user or "", options=opts):
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                t = "".join(getattr(b, "text", "") for b in content)
                if t:
                    text = t
            result = getattr(msg, "result", None)
            if isinstance(result, str) and result.strip():
                text = result  # ResultMessage holds the final answer
    return text


class _Messages:
    async def create(self, **kwargs):
        text = await _run_subscription(
            _system_text(kwargs.get("system")),
            _user_text(kwargs.get("messages")),
            kwargs.get("model"),
        )
        return _Resp(text)


class SubscriptionAnthropic:
    """Minimal stand-in for anthropic.AsyncAnthropic backed by the Max subscription."""

    def __init__(self, *args, **kwargs):
        self.messages = _Messages()


def make_client():
    if settings.LLM_BACKEND == "subscription":
        return SubscriptionAnthropic()
    import anthropic
    return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
