"""Audio boundary protection: 30ms fades to prevent zero-crossing clicks and pops."""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Sequence, Tuple
import soundfile as sf
import numpy as np


def apply_boundary_fades(
    samples: np.ndarray,
    sr: int = 48000,
    fade_duration_s: float = 0.03,  # 30ms fade
) -> np.ndarray:
    """
    Apply a 30ms cosine or linear fade-in at the start and fade-out at the end
    to eliminate zero-crossing clicks and pops at cut boundaries (Hard Rule 3).
    """
    out = samples.copy()
    fade_samples = int(fade_duration_s * sr)

    if len(out) < 2 * fade_samples:
        fade_samples = len(out) // 2

    if fade_samples <= 0:
        return out

    # Half-cosine (smooth S-curve) fade curve
    t = np.linspace(0.0, 1.0, fade_samples, endpoint=True)
    fade_in = 0.5 * (1.0 - np.cos(np.pi * t))
    fade_out = 0.5 * (1.0 + np.cos(np.pi * t))

    if out.ndim == 1:
        out[:fade_samples] *= fade_in
        out[-fade_samples:] *= fade_out
    else:
        out[:fade_samples, :] *= fade_in[:, np.newaxis]
        out[-fade_samples:, :] *= fade_out[:, np.newaxis]

    return out


def apply_boundary_fades_to_file(
    input_path: Path | str,
    output_path: Path | str,
    fade_duration_s: float = 0.03,
) -> Path:
    """Read an audio file, apply 30ms boundary protection fades, and write to output_path."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data, sr = sf.read(str(input_path))
    protected = apply_boundary_fades(data, sr=sr, fade_duration_s=fade_duration_s)
    sf.write(str(output_path), protected, sr)
    return output_path

