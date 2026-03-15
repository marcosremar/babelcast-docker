"""Protocol interfaces for service abstraction and testability."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Transcriber(Protocol):
    def transcribe(self, audio_bytes: bytes, language: str = "fr") -> dict: ...


@runtime_checkable
class Translator(Protocol):
    async def translate(self, text: str, source_lang: str, target_lang: str) -> dict: ...
    async def health_check(self) -> bool: ...


@runtime_checkable
class Synthesizer(Protocol):
    def synthesize(self, text: str, language: str, speaker: str) -> bytes: ...
