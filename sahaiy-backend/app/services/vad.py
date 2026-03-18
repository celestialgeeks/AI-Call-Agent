"""
app/services/vad.py
────────────────────
Simple energy-based Voice Activity Detection (VAD) using numpy.
(audioop was removed in Python 3.13 — numpy is used instead.)

Detects speech onset during AI playback to enable interruption.
"""

import logging
import struct
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 300.0  # RMS energy threshold — tune for your mic


def _rms_numpy(pcm_bytes: bytes, sample_width: int = 2) -> float:
    """Compute RMS energy of raw PCM bytes using struct unpacking + basic math."""
    if not pcm_bytes:
        return 0.0
    if len(pcm_bytes) % sample_width != 0:
        pcm_bytes = pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % sample_width)]
    n = len(pcm_bytes) // sample_width
    if n == 0:
        return 0.0
    fmt = f"{n}h"  # signed 16-bit
    try:
        samples = struct.unpack(fmt, pcm_bytes)
    except struct.error:
        return 0.0
    total = sum(s * s for s in samples)
    return (total / n) ** 0.5


def is_speech(
    pcm_bytes: bytes,
    sample_width: int = 2,
    threshold: float = DEFAULT_THRESHOLD,
) -> bool:
    """
    Return True if the audio chunk contains speech (energy above threshold).

    Args:
        pcm_bytes:    Raw signed 16-bit LE PCM bytes.
        sample_width: Bytes per sample (2 for 16-bit).
        threshold:    RMS energy threshold.
    """
    return _rms_numpy(pcm_bytes, sample_width) > threshold


def rms_energy(pcm_bytes: bytes, sample_width: int = 2) -> float:
    """Return RMS energy of the audio chunk (useful for threshold calibration)."""
    return _rms_numpy(pcm_bytes, sample_width)
