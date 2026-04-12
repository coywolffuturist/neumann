"""Base Formatter interface. All formatters implement this."""
from __future__ import annotations
from abc import ABC, abstractmethod
from ..types import Token, RenderContext


class Formatter(ABC):
    @abstractmethod
    def render(self, token: Token, context: RenderContext) -> str:
        """Render a token for the given context. Pure function."""
        ...
