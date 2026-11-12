"""Procedural audio synthesis, multi-track mixing, and sound design engine."""

from .sfx_synth import (
    SR,
    bandnoise,
    click,
    key,
    blip,
    pop,
    chime,
    whoosh,
    riser,
    impact,
    scratch,
    envelope,
)
from .mixer import AudioMixer
from .engine import SoundDesignEngine

__all__ = [
    "SR",
    "bandnoise",
    "click",
    "key",
    "blip",
    "pop",
    "chime",
    "whoosh",
    "riser",
    "impact",
    "scratch",
    "envelope",
    "AudioMixer",
    "SoundDesignEngine",
]

