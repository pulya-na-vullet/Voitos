from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatMessageDTO:
    role: str
    text: str


@dataclass
class CompletionResult:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract LLM provider — swap implementations without rewriting the app."""

    name: str = "base"

    @abstractmethod
    def complete(
        self,
        messages: list[ChatMessageDTO],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> CompletionResult:
        raise NotImplementedError

    def complete_text(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        result = self.complete(
            [
                ChatMessageDTO(role="system", text=system),
                ChatMessageDTO(role="user", text=user),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return result.text.strip()


class SpeechToTextProvider(ABC):
    name: str = "base"

    @abstractmethod
    def transcribe(self, audio_bytes: bytes, *, lang: str = "ru-RU", audio_format: str = "oggopus") -> str:
        raise NotImplementedError
