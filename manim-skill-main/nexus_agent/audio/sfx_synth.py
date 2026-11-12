"""Procedural sound effects synthesis engine for educational videos.

Adapted from Claude Motion (MIT License), this module generates audio cues
purely algorithmically using mathematical signal modeling (waveforms, noise
filters, envelope decays) without requiring external audio samples or assets.
"""

from __future__ import annotations

import bisect
import math
import random
from typing import Callable, List, Sequence, Tuple

SR: int = 48000  # Broadcast standard sample rate (48 kHz)


def _bound_signal(samples: List[float], max_allowed: float = 0.98) -> List[float]:
    """Ensure raw synthesized signal never exceeds max_allowed peak amplitude to prevent clipping."""
    if not samples:
        return samples
    peak = max(abs(x) for x in samples)
    if peak > max_allowed:
        scale = max_allowed / peak
        return [x * scale for x in samples]
    return samples


def bandnoise(n: int, fc_at: Callable[[int], float], q: float = 0.8) -> List[float]:
    """White noise through a 2-pole state-variable bandpass filter."""
    low = band = 0.0
    out: List[float] = []
    for k in range(n):
        f = 2 * math.sin(math.pi * min(fc_at(k), SR / 6) / SR)
        x = random.uniform(-1, 1)
        low += f * band
        high = x - low - q * band
        band += f * high
        out.append(band)
    return out


def click(freq: float = 1800.0, dur: float = 0.05) -> List[float]:
    """Crisp UI tick / snapping event. Higher freq sounds smaller and lighter."""
    n = int(dur * SR)
    out: List[float] = []
    for k in range(n):
        tt = k / SR
        s = math.sin(2 * math.pi * freq * tt) * math.exp(-tt / 0.012)
        s += random.uniform(-1, 1) * math.exp(-tt / 0.0015) * 0.6
        out.append(s)
    return _bound_signal(out)


def key() -> List[float]:
    """Single mechanical keystroke for typing animations."""
    n = int(0.018 * SR)
    fc = random.uniform(2400, 4200)
    noise = bandnoise(n, lambda k: fc, q=0.5)
    thock = random.uniform(160, 220)
    samples = [
        noise[k] * math.exp(-k / (0.0025 * SR)) * 2.2
        + math.sin(2 * math.pi * thock * k / SR) * math.exp(-k / (0.006 * SR)) * 0.5
        for k in range(n)
    ]
    return _bound_signal(samples)


def blip(freq: float = 440.0, dur: float = 0.30, tau: float = 0.10) -> List[float]:
    """Soft tonal blip for element reveals, label reveals, and data points."""
    n = int(dur * SR)
    out: List[float] = []
    ph = 0.0
    for k in range(n):
        tt = k / SR
        ph += 2 * math.pi * freq / SR
        a = min(1.0, tt / 0.003) * math.exp(-tt / tau)
        out.append(a * (math.sin(ph) + 0.25 * math.sin(2 * ph) + 0.08 * math.sin(3 * ph)))
    return _bound_signal(out)


def pop(f0: float = 650.0, f1: float = 280.0, dur: float = 0.15) -> List[float]:
    """Pitched pop for nodes, badges, or callouts popping into the scene."""
    n = int(dur * SR)
    out: List[float] = []
    ph = 0.0
    for k in range(n):
        tt = k / SR
        f = f0 * (f1 / f0) ** min(1.0, tt / 0.06)
        ph += 2 * math.pi * f / SR
        a = min(1.0, tt / 0.002) * math.exp(-tt / 0.045)
        out.append(a * math.sin(ph))
    return _bound_signal(out)


def chime(freqs: Sequence[float] = (523.25, 659.25, 783.99, 1046.50), dur: float = 1.6, tau: float = 0.6) -> List[float]:
    """Harmonic bell chord for success, completion, or key payoff moments."""
    n = int(dur * SR)
    out = [0.0] * n
    for j, f in enumerate(freqs):
        ph1 = ph2 = 0.0
        g = 1.0 / (1.0 + j * 0.35)
        for k in range(n):
            tt = k / SR
            ph1 += 2 * math.pi * f / SR
            ph2 += 2 * math.pi * f * 2.76 / SR
            a = min(1.0, tt / 0.004) * math.exp(-tt / tau)
            out[k] += g * a * (math.sin(ph1) + 0.18 * math.sin(ph2) * math.exp(-tt / 0.15))
    return _bound_signal(out)


def whoosh(dur: float = 0.40, f0: float = 350.0, f1: float = 1800.0, peak: float = 0.5, q: float = 0.85) -> List[float]:
    """Air whoosh for scene transitions, layer slides, and camera motion."""
    n = int(dur * SR)
    p = math.log(0.5) / math.log(max(0.01, min(0.99, peak)))
    noise = bandnoise(n, lambda k: f0 * (f1 / f0) ** (k / n), q=q)
    samples = [noise[k] * math.sin(math.pi * (k / n) ** p) ** 2 * 1.5 for k in range(n)]
    return _bound_signal(samples)


def riser(dur: float = 1.2, f0: float = 240.0, f1: float = 4800.0) -> List[float]:
    """Continuous upward sweep building anticipation into a major transition."""
    n = int(dur * SR)
    noise = bandnoise(n, lambda k: f0 * (f1 / f0) ** (k / n), q=0.6)
    out: List[float] = []
    ph = 0.0
    for k in range(n):
        x = k / n
        ph += 2 * math.pi * (180.0 * (4.0 ** x)) / SR
        tail = min(1.0, (n - k) / (0.02 * SR))
        out.append((noise[k] * 1.2 + 0.30 * math.sin(ph)) * (x ** 2.2) * tail)
    return _bound_signal(out)


def impact(dur: float = 0.8) -> List[float]:
    """Low-frequency grounded impact for major arrivals or conclusion."""
    n = int(dur * SR)
    out: List[float] = []
    ph = 0.0
    lp = 0.0
    for k in range(n):
        tt = k / SR
        f = 45.0 + 60.0 * math.exp(-tt / 0.06)
        ph += 2 * math.pi * f / SR
        lp += 0.08 * (random.uniform(-1, 1) - lp)
        s = math.sin(ph) * math.exp(-tt / 0.32)
        s += lp * math.exp(-tt / 0.05) * 2.5
        out.append(math.tanh(1.5 * s))
    return _bound_signal(out)


def scratch(dur: float = 0.6) -> List[float]:
    """Soft pencil/chalk texture for lines drawing onto the canvas."""
    n = int(dur * SR)
    noise = bandnoise(n, lambda k: 3400 + 1200 * math.sin(k / SR * 20), q=0.35)
    out: List[float] = []
    for k in range(n):
        tt = k / SR
        stroke = max(0.0, math.sin(tt * 2 * math.pi * 7.5)) ** 3
        env = math.sin(math.pi * k / n)
        out.append(noise[k] * stroke * env * 1.2)
    return _bound_signal(out)


def envelope(points: Sequence[Tuple[float, float]]) -> Callable[[float], float]:
    """Helper: Piecewise-linear gain curve from [(t, value), ...]."""
    pts = sorted(points)
    ts = [p[0] for p in pts]

    def at(t: float) -> float:
        i = bisect.bisect_right(ts, t)
        if i == 0:
            return pts[0][1]
        if i == len(pts):
            return pts[-1][1]
        (t0, v0), (t1, v1) = pts[i - 1], pts[i]
        if t1 == t0:
            return v0
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    return at

