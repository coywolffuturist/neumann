"""PlainTextRenderer and FallbackHandler."""
from __future__ import annotations
from ..types import Token, RenderContext
from . import Formatter


class PlainTextRenderer(Formatter):
    def render(self, token: Token, context: RenderContext) -> str:
        if context == RenderContext.WEB_HTML:
            escaped = token.raw.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            return f"<p>{escaped}</p>"
        return token.raw


class FallbackHandler(Formatter):
    """Last resort — returns raw input with a warning comment in terminal mode."""
    def render(self, token: Token, context: RenderContext) -> str:
        if context == RenderContext.TERMINAL:
            return f"\033[2m[neumann:fallback type={token.type.value}]\033[0m\n{token.raw}"
        return token.raw
