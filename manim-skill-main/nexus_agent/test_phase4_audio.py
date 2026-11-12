"""Test suite for Phase 4.1: Procedural Audio and Sound Design Engine."""

import sys
import subprocess
from pathlib import Path
import numpy as np
import soundfile as sf

from nexus_agent.audio import (
    SR,
    click,
    blip,
    pop,
    chime,
    whoosh,
    riser,
    impact,
    scratch,
    AudioMixer,
    SoundDesignEngine,
)


def test_procedural_generators():
    print("Test 1: Verifying procedural sound generators...")
    generators = [
        ("click", click(1800, 0.05), 0.05),
        ("blip", blip(440, 0.30), 0.30),
        ("pop", pop(650, 280, 0.15), 0.15),
        ("chime", chime([523.25, 659.25], 1.6), 1.6),
        ("whoosh", whoosh(0.40, 350, 1800), 0.40),
        ("riser", riser(1.0), 1.0),
        ("impact", impact(0.8), 0.8),
        ("scratch", scratch(0.6), 0.6),
    ]

    for name, samples, expected_dur in generators:
        assert len(samples) > 0, f"Generator {name} produced empty samples"
        assert not any(np.isnan(samples)), f"Generator {name} produced NaNs"
        assert not any(np.isinf(samples)), f"Generator {name} produced Infs"
        peak = max(abs(x) for x in samples)
        assert peak > 0.0, f"Generator {name} produced pure silence"
        actual_dur = len(samples) / SR
        assert abs(actual_dur - expected_dur) < 0.01, f"{name} duration mismatch: {actual_dur} vs {expected_dur}"
        print(f"  [PASS] {name:8s}: {len(samples):6d} samples ({actual_dur:.2f}s) | peak={peak:.3f}")


def test_speech_ducking():
    print("\nTest 2: Verifying narration-aware speech ducking...")
    mixer = AudioMixer(duration_s=5.0)

    # Synthetic narration: active speech from 1.0s to 3.0s
    t = np.linspace(0, 5, 5 * SR, endpoint=False)
    synthetic_speech = np.zeros_like(t)
    speech_mask = (t >= 1.0) & (t <= 3.0)
    synthetic_speech[speech_mask] = 0.5 * np.sin(2 * np.pi * 300 * t[speech_mask])

    mixer.add_narration(synthetic_speech, source_sr=SR)
    assert len(mixer.speech_intervals) > 0, "No speech interval detected"
    print(f"  Speech intervals detected: {mixer.speech_intervals}")

    # Check speech detection
    assert mixer.is_speech_active(2.0) is True, "Expected speech active at t=2.0s"
    assert mixer.is_speech_active(4.0) is False, "Expected speech inactive at t=4.0s"

    # Put a blip at t=2.0s (during speech) and t=4.0s (during pause)
    test_cue = [1.0] * int(0.1 * SR)
    mixer.put_sfx(2.0, test_cue, gain=1.0, allow_ducking=True)
    mixer.put_sfx(4.0, test_cue, gain=1.0, allow_ducking=True)

    idx_speech = int(2.05 * SR)
    idx_pause = int(4.05 * SR)

    sfx_level_during_speech = abs(mixer.sfx_left[idx_speech])
    sfx_level_during_pause = abs(mixer.sfx_left[idx_pause])

    print(f"  SFX amplitude during speech: {sfx_level_during_speech:.3f}")
    print(f"  SFX amplitude during pause:  {sfx_level_during_pause:.3f}")
    assert sfx_level_during_speech < sfx_level_during_pause, "SFX was not ducked during active speech!"
    attenuation_db = 20 * np.log10(sfx_level_during_speech / sfx_level_during_pause)
    print(f"  [PASS] Speech ducking verified: {attenuation_db:.1f} dB attenuation during speech.")


def test_full_pipeline_and_sample_video():
    print("\nTest 3: Generating complete sound score and sample video...")
    timeline_file = Path("scene_timeline.json")
    voiceover_file = Path("neural_network_voiceover.mp3")
    video_source = Path("neural_network_synced.mp4")

    engine = SoundDesignEngine(
        timeline_path=timeline_file,
        voiceover_path=voiceover_file,
    )

    # 1. Synthesize sound score
    score_result = engine.generate_sound_score()
    print(f"  Total cues synthesized: {score_result['total_cues_placed']}")

    # 2. Export mastered WAV and MP3
    out_wav = Path("neural_network_audio_master.wav")
    out_mp3 = Path("neural_network_audio_master.mp3")
    engine.export_audio_master(out_wav, out_mp3)

    assert out_wav.exists() and out_wav.stat().st_size > 0, "Master WAV missing"
    assert out_mp3.exists() and out_mp3.stat().st_size > 0, "Master MP3 missing"

    # Verify duration
    wav_info = sf.info(str(out_wav))
    print(f"  Master audio duration: {wav_info.duration:.2f}s | Sample rate: {wav_info.samplerate}Hz | Channels: {wav_info.channels}")
    assert abs(wav_info.duration - 38.1) < 1.0, f"Unexpected master duration: {wav_info.duration}"

    # 3. Mux with video to produce sample video
    sample_video = Path("neural_network_with_sfx.mp4")
    engine.mux_with_video(
        video_source=video_source,
        output_video=sample_video,
        master_audio=out_wav,
    )

    assert sample_video.exists() and sample_video.stat().st_size > 0, "Sample video not created"
    print(f"  [PASS] Sample video created: {sample_video.name} ({sample_video.stat().st_size:,} bytes)")

    # 4. Probe sample video streams
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name",
        "-of", "json",
        str(sample_video),
    ]
    probe_out = subprocess.check_output(probe_cmd, text=True)
    print(f"  Probe verification:\n{probe_out}")


def main():
    print("================================================================")
    print("Testing Phase 4.1: Procedural Audio and Sound Design Engine")
    print("================================================================")

    test_procedural_generators()
    test_speech_ducking()
    test_full_pipeline_and_sample_video()

    print("\n================================================================")
    print("Phase 4.1 Test Suite Complete: ALL TESTS PASSED!")
    print("================================================================")


if __name__ == "__main__":
    main()

