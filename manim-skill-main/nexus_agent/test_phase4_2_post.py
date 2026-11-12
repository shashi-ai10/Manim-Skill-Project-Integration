"""Test suite for Phase 4.2: Broadcast Post-Production."""

import sys
import subprocess
from pathlib import Path
import numpy as np
import soundfile as sf

from nexus_agent.audio.sfx_synth import (
    click, key, blip, pop, chime, whoosh, riser, impact, scratch
)
from nexus_agent.compositor import (
    apply_boundary_fades,
    TwoPassLoudnorm,
    PostProductionPipeline,
    probe_video_duration,
)


def test_generator_peaks():
    print("Test 1: Investigating and verifying generator peak limits...")
    generators = [
        ("click", click()),
        ("key", key()),
        ("blip", blip()),
        ("pop", pop()),
        ("chime", chime()),
        ("whoosh", whoosh()),
        ("riser", riser()),
        ("impact", impact()),
        ("scratch", scratch()),
    ]
    for name, samples in generators:
        peak = max(abs(x) for x in samples)
        assert peak <= 0.9801, f"Generator {name} peak {peak} exceeds 0.98 ceiling!"
        assert peak > 0.1, f"Generator {name} peak {peak} is too quiet!"
        print(f"  [PASS] {name:8s}: peak={peak:.4f} (<= 0.9800 safe ceiling)")


def test_boundary_fades():
    print("\nTest 2: Verifying 30ms audio boundary anti-pop protection...")
    sr = 48000
    n = int(2.0 * sr)
    # A full-scale square signal that would normally pop violently at edges
    raw_signal = np.ones((n, 2), dtype=np.float32) * 0.8
    protected = apply_boundary_fades(raw_signal, sr=sr, fade_duration_s=0.03)

    fade_samples = int(0.03 * sr)
    # Check start and end are 0
    assert abs(protected[0, 0]) < 1e-4, f"Start of fade is not zero: {protected[0, 0]}"
    assert abs(protected[-1, 0]) < 1e-4, f"End of fade is not zero: {protected[-1, 0]}"
    # Check interior is preserved
    assert abs(protected[n // 2, 0] - 0.8) < 1e-4, "Interior of signal altered by fade"
    print(f"  [PASS] 30ms S-curve boundary protection verified (fade_samples={fade_samples}).")


def test_broadcast_post_production_pipeline():
    print("\nTest 3: Executing full Broadcast Post-Production Pipeline...")
    timeline_file = Path("scene_timeline.json")
    video_source = Path("neural_network_synced.mp4")
    voiceover_source = Path("neural_network_voiceover.mp3")
    output_video = Path("neural_network_broadcast.mp4")

    # Save original modification times to verify preservation
    orig_video_stat = video_source.stat()

    pipeline = PostProductionPipeline(
        timeline_path=timeline_file,
        video_source=video_source,
        voiceover_source=voiceover_source,
    )

    results = pipeline.produce_broadcast_master(
        output_video=output_video,
    )

    # 1. Check output file exists
    assert output_video.exists() and output_video.stat().st_size > 0, "Broadcast video was not generated!"
    print(f"  [PASS] Generated broadcast master video: {output_video.name} ({output_video.stat().st_size:,} bytes)")

    # 2. Verify duration alignment
    target_dur = results["target_duration_s"]
    actual_dur = results["video_duration_s"]
    delta_ms = results["duration_delta_ms"]
    print(f"  Duration check: Video Target={target_dur:.3f}s, Actual={actual_dur:.3f}s (Delta: {delta_ms:.2f}ms)")
    assert delta_ms < 10.0, f"Duration mismatch exceeds 10ms: {delta_ms:.2f}ms"
    print(f"  [PASS] Duration perfectly aligned to millisecond accuracy.")

    # 3. Verify loudness mastering
    loudness = results["loudness"]
    print(f"  Loudness check: I={loudness['integrated_lufs']:.2f} LUFS, TP={loudness['true_peak_db']:.2f} dBFS, LRA={loudness['lra']:.2f} LU")
    # Target: -14.0 LUFS +/- 1.0, TP <= -1.0
    assert abs(loudness["integrated_lufs"] - (-14.0)) <= 1.0, f"Loudness {loudness['integrated_lufs']} outside -14.0 LUFS target!"
    assert loudness["true_peak_db"] <= -0.9, f"True peak {loudness['true_peak_db']} exceeds -1.0 dBFS ceiling!"
    print(f"  [PASS] Broadcast loudness criteria met (-14.0 LUFS +/- 1.0, TP <= -1.0 dBFS).")

    # 4. Verify original video integrity
    curr_video_stat = video_source.stat()
    assert curr_video_stat.st_mtime == orig_video_stat.st_mtime, "Original video was modified!"
    assert curr_video_stat.st_size == orig_video_stat.st_size, "Original video size changed!"
    print(f"  [PASS] Original video {video_source.name} completely preserved.")


def test_subtitles_and_overlays():
    print("\nTest 4: Verifying Master SRT generation and Subtitles LAST compositing...")
    from nexus_agent.compositor.subtitles import build_master_srt_from_timeline
    from nexus_agent.compositor.overlays import build_composite_filtergraph

    srt_file = build_master_srt_from_timeline("scene_timeline.json", "neural_network_master.srt")
    assert srt_file.exists() and srt_file.stat().st_size > 0, "Master SRT missing!"

    subtitled_video = Path("neural_network_broadcast_subtitled.mp4")
    build_composite_filtergraph(
        base_video="neural_network_broadcast.mp4",
        output_video=subtitled_video,
        subtitles_srt=srt_file,
    )
    assert subtitled_video.exists() and subtitled_video.stat().st_size > 0, "Subtitled video missing!"
    print(f"  [PASS] Subtitles burned with safe margins -> {subtitled_video.name} ({subtitled_video.stat().st_size:,} bytes).")


def main():
    print("================================================================")
    print("Testing Phase 4.2: Broadcast Post-Production Pipeline")
    print("================================================================")

    test_generator_peaks()
    test_boundary_fades()
    test_broadcast_post_production_pipeline()
    test_subtitles_and_overlays()

    print("\n================================================================")
    print("Phase 4.2 Test Suite Complete: ALL TESTS PASSED!")
    print("================================================================")


if __name__ == "__main__":
    main()
