"""Speaker identification using pyannote.audio embeddings.

Extracts speaker embeddings from audio chunks and verifies if they
belong to the same speaker via cosine similarity. Used by
VoiceProfileManager to collect only the target speaker's voice.

Requires: pip install pyannote.audio
Model: pyannote/embedding (gated — needs HF token + license acceptance)
"""

import logging
import numpy as np
import torch

logger = logging.getLogger(__name__)

# Default cosine similarity threshold for same-speaker verification.
# Benchmark (gTTS voices): same speaker 0.69–0.86, different speakers -0.14–0.35.
# At 0.60: 100% accuracy. At 0.75: 92% (rejects some target chunks).
DEFAULT_THRESHOLD = 0.60


class SpeakerVerifier:
    """Extract and compare speaker embeddings using pyannote/embedding."""

    def __init__(self, device: str = "cpu", hf_token: str = ""):
        self._device = device
        self._hf_token = hf_token
        self._inference = None

    def load(self):
        from pyannote.audio import Model, Inference

        logger.info("Loading speaker embedding model (pyannote/embedding)...")
        model = Model.from_pretrained(
            "pyannote/embedding",
            use_auth_token=self._hf_token or None,
        )
        self._inference = Inference(model, window="whole")
        self._inference.to(torch.device(self._device))
        logger.info("Speaker embedding model loaded on %s", self._device)

    def extract_embedding(self, audio: np.ndarray,
                          sample_rate: int = 16000) -> np.ndarray:
        """Extract speaker embedding from float32 mono audio.

        Returns a 1-D numpy array (the embedding vector).
        """
        if self._inference is None:
            self.load()

        # pyannote Inference accepts {"waveform": (1, T), "sample_rate": int}
        waveform = torch.tensor(audio, dtype=torch.float32).unsqueeze(0)
        embedding = self._inference({"waveform": waveform, "sample_rate": sample_rate})
        return embedding.flatten()

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two embedding vectors."""
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm < 1e-8:
            return 0.0
        return float(np.dot(a, b) / norm)

    def is_same_speaker(self, audio: np.ndarray, ref_embedding: np.ndarray,
                        sample_rate: int = 16000,
                        threshold: float = DEFAULT_THRESHOLD) -> tuple[bool, float]:
        """Check if audio belongs to the same speaker as ref_embedding.

        Returns (is_same, similarity_score).
        """
        embedding = self.extract_embedding(audio, sample_rate)
        similarity = self.cosine_similarity(embedding, ref_embedding)
        return similarity >= threshold, similarity
