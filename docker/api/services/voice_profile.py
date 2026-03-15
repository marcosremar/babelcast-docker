"""Voice profile manager — accumulates speaker audio for voice cloning.

Collects raw audio + transcripts from the first ~15s of a session,
then locks the profile so all subsequent TTS uses the cloned voice.

Speaker verification (pyannote/embedding) ensures that only audio from
the identified target speaker is accumulated — other voices in the room
are rejected.
"""

import logging
import threading
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from services.speaker_id import SpeakerVerifier

logger = logging.getLogger(__name__)


class ProfileState(str, Enum):
    COLLECTING = "collecting"
    LOCKED = "locked"


class VoiceProfileManager:
    """Thread-safe voice profile that accumulates reference audio for cloning.

    When a SpeakerVerifier is provided, only audio matching the target
    speaker (identified from the first chunk) is accepted.
    """

    def __init__(self, min_audio_s: float = 15.0, sample_rate: int = 16000,
                 verifier: "SpeakerVerifier | None" = None,
                 verify_threshold: float = 0.75):
        self._min_audio_s = min_audio_s
        self._sample_rate = sample_rate
        self._verifier = verifier
        self._verify_threshold = verify_threshold
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._texts: list[str] = []
        self._total_samples: int = 0
        self._state = ProfileState.COLLECTING
        # Cached concatenated reference (set on lock)
        self._ref_audio: np.ndarray | None = None
        self._ref_text: str = ""
        # Speaker identification
        self._speaker_embedding: np.ndarray | None = None
        self._rejected_chunks: int = 0

    @property
    def state(self) -> ProfileState:
        return self._state

    @property
    def is_locked(self) -> bool:
        return self._state == ProfileState.LOCKED

    @property
    def duration_s(self) -> float:
        return self._total_samples / self._sample_rate

    @property
    def ref_audio(self) -> np.ndarray | None:
        return self._ref_audio

    @property
    def ref_text(self) -> str:
        return self._ref_text

    def feed(self, audio: np.ndarray, text: str, sample_rate: int = 16000) -> bool:
        """Feed a chunk of audio + its transcript. Returns True if profile just locked.

        audio: float32 numpy array (mono, 16kHz)
        text: Whisper transcript of this audio chunk

        If a SpeakerVerifier is set, the first chunk establishes the target
        speaker identity. Subsequent chunks are rejected if they don't match.
        """
        with self._lock:
            if self._state == ProfileState.LOCKED:
                return False

            if not text or not text.strip():
                return False

            # Resample if needed
            if sample_rate != self._sample_rate:
                ratio = self._sample_rate / sample_rate
                new_len = int(len(audio) * ratio)
                indices = np.linspace(0, len(audio) - 1, new_len).astype(int)
                audio = audio[indices]
                sample_rate = self._sample_rate

            # ── Speaker verification ──────────────────────────────────
            if self._verifier is not None:
                try:
                    if self._speaker_embedding is None:
                        # First chunk: establish target speaker identity
                        self._speaker_embedding = self._verifier.extract_embedding(
                            audio, sample_rate)
                        logger.info("Speaker ID: target speaker identified from first chunk "
                                    "(embedding dim=%d)", len(self._speaker_embedding))
                    else:
                        # Subsequent chunks: verify speaker
                        is_same, similarity = self._verifier.is_same_speaker(
                            audio, self._speaker_embedding,
                            sample_rate=sample_rate,
                            threshold=self._verify_threshold,
                        )
                        if not is_same:
                            self._rejected_chunks += 1
                            logger.info(
                                "Speaker ID: chunk REJECTED (similarity=%.3f < %.2f) "
                                "— different speaker detected (total rejected=%d)",
                                similarity, self._verify_threshold, self._rejected_chunks,
                            )
                            return False
                        logger.debug("Speaker ID: chunk accepted (similarity=%.3f)", similarity)
                except Exception as e:
                    # Don't block voice collection if speaker ID fails
                    logger.warning("Speaker ID: verification failed (%s), accepting chunk", e)

            # ── Accumulate audio ──────────────────────────────────────
            self._chunks.append(audio)
            self._texts.append(text.strip())
            self._total_samples += len(audio)

            duration = self._total_samples / self._sample_rate
            logger.info("Voice profile: %.1fs / %.1fs collected", duration, self._min_audio_s)

            if duration >= self._min_audio_s:
                self._lock_profile()
                return True

            return False

    def _lock_profile(self):
        """Concatenate all chunks and lock the profile."""
        self._ref_audio = np.concatenate(self._chunks)
        # Use only the first transcript as ref_text — passing ALL accumulated
        # text causes generate_voice_clone to use it as a duration/pacing guide,
        # producing wildly oversized audio (e.g. 161s for a 5s phrase).
        self._ref_text = self._texts[0] if self._texts else ""
        self._state = ProfileState.LOCKED
        duration = len(self._ref_audio) / self._sample_rate
        logger.info(
            "Voice profile LOCKED: %.1fs audio, %d chars text, %d chunks rejected",
            duration, len(self._ref_text), self._rejected_chunks,
        )
        # Free individual chunks
        self._chunks.clear()
        self._texts.clear()

    def reset(self):
        """Reset to collecting state (e.g., new speaker)."""
        with self._lock:
            self._chunks.clear()
            self._texts.clear()
            self._total_samples = 0
            self._state = ProfileState.COLLECTING
            self._ref_audio = None
            self._ref_text = ""
            self._speaker_embedding = None
            self._rejected_chunks = 0
            logger.info("Voice profile reset to COLLECTING")

    def status(self) -> dict:
        return {
            "state": self._state.value,
            "duration_s": round(self.duration_s, 1),
            "min_duration_s": self._min_audio_s,
            "is_locked": self.is_locked,
            "speaker_identified": self._speaker_embedding is not None,
            "rejected_chunks": self._rejected_chunks,
        }
