"""Multi-track audio mixer with narration-aware ducking and ITU-R BS.1770 mastering."""

from __future__ import annotations

import math
import os
import random
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import soundfile as sf

from nexus_agent.tools_resolver import get_ffmpeg_path, run_command_diagnostics
from .sfx_synth import SR
from . import dsp


# K-weighting filters for BS.1770 integrated loudness measurement
_SHELF = (
    (1.53512485958697, -2.69169618940638, 1.19839281085285),
    (-1.69065929318241, 0.73248077421585),
)
_HIGHPASS = (
    (1.0, -2.0, 1.0),
    (-1.99004745483398, 0.99007225036621),
)


def _biquad(x: Sequence[float], coeffs: Tuple[Tuple[float, float, float], Tuple[float, float]]) -> List[float]:
    (b0, b1, b2), (a1, a2) = coeffs
    y = [0.0] * len(x)
    x1 = x2 = y1 = y2 = 0.0
    for i, xi in enumerate(x):
        yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, xi, y1, yi
        y[i] = yi
    return y


def _compute_lufs(left: Sequence[float], right: Sequence[float]) -> float:
    """Calculate integrated loudness in LUFS according to ITU-R BS.1770-4."""
    kl = _biquad(_biquad(left, _SHELF), _HIGHPASS)
    kr = _biquad(_biquad(right, _SHELF), _HIGHPASS)

    cs = [0.0]
    acc = 0.0
    for a, b in zip(kl, kr):
        acc += a * a + b * b
        cs.append(acc)

    block, hop = int(0.4 * SR), int(0.1 * SR)
    if len(kl) < block:
        return -70.0
    z = [(cs[i + block] - cs[i]) / block for i in range(0, len(kl) - block + 1, hop)]

    def _power_to_lufs(power: float) -> float:
        return -0.691 + 10.0 * math.log10(max(1e-12, power))

    above_abs = [p for p in z if p > 0 and _power_to_lufs(p) > -70.0]
    if not above_abs:
        return -70.0

    relative = _power_to_lufs(sum(above_abs) / len(above_abs)) - 10.0
    gated = [p for p in above_abs if _power_to_lufs(p) > relative]
    if not gated:
        return -70.0
    return _power_to_lufs(sum(gated) / len(gated))


def _compute_true_peak(samples: Sequence[float]) -> float:
    """Approximate true peak by checking absolute amplitude peaks."""
    if not samples:
        return 0.0
    peak = max(abs(x) for x in samples)
    return peak * 1.05  # 5% headroom margin for inter-sample peaks


class AudioMixer:
    """Multi-track audio mixer supporting narration beds, SFX cues, and dynamic ducking."""

    def __init__(self, duration_s: float, seed: int = 42):
        random.seed(seed)
        self.duration_s = max(0.1, duration_s)
        self.n_samples = int(SR * self.duration_s)

        self.narration_left = [0.0] * self.n_samples
        self.narration_right = [0.0] * self.n_samples

        self.sfx_left = [0.0] * self.n_samples
        self.sfx_right = [0.0] * self.n_samples

        self.speech_intervals: List[Tuple[float, float]] = []
        self._mastered_left: Optional[List[float]] = None
        self._mastered_right: Optional[List[float]] = None

    def add_narration(
        self,
        audio_source: Path | str | np.ndarray,
        source_sr: Optional[int] = None,
        gain: float = 1.0,
        activity_threshold: float = 0.02,
    ) -> None:
        """
        Load voiceover speech into the narration bed and detect speech intervals for ducking.
        """
        if isinstance(audio_source, (str, Path)):
            audio_path = Path(audio_source)
            data, sr = sf.read(str(audio_path))
        else:
            data = audio_source
            sr = source_sr or SR

        # Downmix to mono if stereo
        if data.ndim > 1:
            data = np.mean(data, axis=1)

        # Resample to 48kHz
        if sr != SR:
            indices = np.linspace(0, len(data) - 1, int(len(data) * (SR / sr)))
            data = np.interp(indices, np.arange(len(data)), data)

        data = data * gain
        usable_len = min(len(data), self.n_samples)

        # Load into narration channels (centered stereo)
        for i in range(usable_len):
            val = float(data[i])
            self.narration_left[i] = val
            self.narration_right[i] = val

        # Detect active speech windows (blocks of 100ms with energy > threshold)
        block_size = int(0.1 * SR)
        for i in range(0, usable_len - block_size, block_size):
            block = data[i : i + block_size]
            rms = np.sqrt(np.mean(block ** 2))
            if rms > activity_threshold:
                t0 = i / SR
                t1 = (i + block_size) / SR
                self.speech_intervals.append((t0, t1))

        # Merge overlapping intervals with a 150ms hold buffer
        merged: List[Tuple[float, float]] = []
        buffer_s = 0.15
        for t0, t1 in sorted(self.speech_intervals):
            if not merged:
                merged.append((max(0.0, t0 - buffer_s), t1 + buffer_s))
            else:
                prev_0, prev_1 = merged[-1]
                if t0 - buffer_s <= prev_1:
                    merged[-1] = (prev_0, max(prev_1, t1 + buffer_s))
                else:
                    merged.append((max(0.0, t0 - buffer_s), t1 + buffer_s))
        self.speech_intervals = merged

    def is_speech_active(self, t_sec: float) -> bool:
        """Check if narration speech is currently active at t_sec."""
        for t0, t1 in self.speech_intervals:
            if t0 <= t_sec <= t1:
                return True
        return False

    def put_sfx(
        self,
        t_sec: float,
        samples: Sequence[float],
        gain: float = 1.0,
        pan: float = 0.0,
        allow_ducking: bool = True,
        ducking_attenuation: float = 0.38,  # -8.4 dB during active speech
    ) -> None:
        """
        Place mono SFX samples at t_sec with spatial panning and speech-aware ducking.
        pan: -1.0 (left), 0.0 (center), +1.0 (right).
        """
        if t_sec < 0 or t_sec >= self.duration_s:
            return

        # Apply ducking if speech is currently active at this timestamp
        effective_gain = gain
        if allow_ducking and self.is_speech_active(t_sec):
            effective_gain *= ducking_attenuation

        # Equal-power panning law
        angle = (pan + 1.0) * math.pi / 4.0
        gl = effective_gain * math.cos(angle)
        gr = effective_gain * math.sin(angle)

        i0 = int(t_sec * SR)
        for k, s in enumerate(samples):
            idx = i0 + k
            if 0 <= idx < self.n_samples:
                self.sfx_left[idx] += s * gl
                self.sfx_right[idx] += s * gr

    def put_spatial_sfx(
        self,
        t_sec: float,
        samples: Sequence[float],
        gain: float = 1.0,
        pan: float = 0.0,
        reverb_mix: float = 0.15,
        allow_ducking: bool = True,
        ducking_attenuation: float = 0.38,
    ) -> None:
        """
        Place SFX samples with spatial panning, Schroeder Freeverb reverb, and ducking.
        Directly uses pure-Python DSP Freeverb from Claude Motion (sfx/dsp.py).
        """
        if t_sec < 0 or t_sec >= self.duration_s:
            return

        effective_gain = gain
        if allow_ducking and self.is_speech_active(t_sec):
            effective_gain *= ducking_attenuation

        angle = (pan + 1.0) * math.pi / 4.0
        gl = effective_gain * math.cos(angle)
        gr = effective_gain * math.sin(angle)

        sl = [s * gl for s in samples]
        sr = [s * gr for s in samples]

        if reverb_mix > 0.0:
            mono_x = [s * effective_gain for s in samples]
            rwl, rwr = dsp.reverb(mono_x, size=0.75, scale=1.2)
            dry_w = 1.0 - reverb_mix
            wet_w = reverb_mix
            n_rev = min(len(sl), len(rwl))
            sl = [sl[i] * dry_w + rwl[i] * wet_w for i in range(n_rev)]
            sr = [sr[i] * dry_w + rwr[i] * wet_w for i in range(n_rev)]

        i0 = int(t_sec * SR)
        for k in range(len(sl)):
            idx = i0 + k
            if 0 <= idx < self.n_samples:
                self.sfx_left[idx] += sl[k]
                self.sfx_right[idx] += sr[k]

    def master(
        self,
        target_lufs: float = -14.0,
        ceiling_db: float = -1.5,
        fade_out_s: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Combine narration and SFX, apply soft tanh limiting, and master to target LUFS.
        """
        combined_l = [n + s for n, s in zip(self.narration_left, self.sfx_left)]
        combined_r = [n + s for n, s in zip(self.narration_right, self.sfx_right)]

        # Apply smooth tail fade-out to prevent abrupt endings
        fade_n = int(fade_out_s * SR)
        for i in range(fade_n):
            idx = self.n_samples - fade_n + i
            factor = (fade_n - i) / fade_n
            combined_l[idx] *= factor
            combined_r[idx] *= factor

        # Measure pre-master loudness
        pre_lufs = _compute_lufs(combined_l, combined_r)
        peak = max(max(abs(x) for x in combined_l), max(abs(x) for x in combined_r)) or 1.0

        # Soft limiting using tanh curve
        ceiling_linear = 10.0 ** (ceiling_db / 20.0)
        drive = 1.3
        norm = math.tanh(drive)
        limited_l = [math.tanh(drive * (x / peak)) / norm for x in combined_l]
        limited_r = [math.tanh(drive * (x / peak)) / norm for x in combined_r]

        curr_lufs = _compute_lufs(limited_l, limited_r)
        gain_adjust = 10.0 ** ((target_lufs - curr_lufs) / 20.0)

        tp = max(_compute_true_peak(limited_l), _compute_true_peak(limited_r))
        final_gain = min(gain_adjust, ceiling_linear / max(1e-4, tp))

        self._mastered_left = [x * final_gain for x in limited_l]
        self._mastered_right = [x * final_gain for x in limited_r]

        final_lufs = _compute_lufs(self._mastered_left, self._mastered_right)
        final_tp = max(_compute_true_peak(self._mastered_left), _compute_true_peak(self._mastered_right))
        final_tp_db = 20.0 * math.log10(max(1e-4, final_tp))

        return {
            "pre_lufs": round(pre_lufs, 2),
            "final_lufs": round(final_lufs, 2),
            "final_tp_db": round(final_tp_db, 2),
            "target_lufs": target_lufs,
            "duration_s": self.duration_s,
        }

    def export_wav(self, output_path: Path | str) -> Path:
        """Write 16-bit 48kHz stereo WAV file."""
        if self._mastered_left is None or self._mastered_right is None:
            self.master()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SR)

            frames = bytearray()
            for l, r in zip(self._mastered_left, self._mastered_right):
                il = int(max(-32767, min(32767, l * 32767)))
                ir = int(max(-32767, min(32767, r * 32767)))
                frames.extend(struct.pack("<hh", il, ir))
            wf.writeframes(frames)

        return output_path

    def export_mp3(self, output_path: Path | str, bitrate: str = "192k") -> Path:
        """Convert mastered audio to MP3 using FFmpeg."""
        output_path = Path(output_path)
        wav_path = output_path.with_suffix(".temp.wav")
        self.export_wav(wav_path)

        ffmpeg_bin = get_ffmpeg_path()
        cmd = [
            ffmpeg_bin, "-y",
            "-i", str(wav_path),
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            "-ar", str(SR),
            str(output_path),
        ]
        run_command_diagnostics(cmd)
        wav_path.unlink(missing_ok=True)
        return output_path

