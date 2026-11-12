"""Regression tests for FFmpeg executable resolution, rich diagnostics, and Stage 8 pipeline recovery."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from nexus_agent.tools_resolver import (
    get_ffmpeg_path,
    get_ffprobe_path,
    resolve_executable,
    run_command_diagnostics,
    validate_media_file,
)
from nexus_agent.sync import TimelineManager, SceneTiming
from nexus_agent.orchestrator import VideoProductionOrchestrator


class TestFFmpegRecovery(unittest.TestCase):
    """Verify tool resolution, error reporting, and recovery mechanics."""

    def test_01_executable_resolution(self):
        """Verify get_ffmpeg_path and get_ffprobe_path return existing binaries."""
        ffmpeg_bin = get_ffmpeg_path()
        ffprobe_bin = get_ffprobe_path()

        self.assertTrue(Path(ffmpeg_bin).exists(), f"Resolved ffmpeg binary does not exist: {ffmpeg_bin}")
        self.assertTrue(Path(ffprobe_bin).exists(), f"Resolved ffprobe binary does not exist: {ffprobe_bin}")
        self.assertTrue(os.path.isfile(ffmpeg_bin))
        self.assertTrue(os.path.isfile(ffprobe_bin))

        # Check that parent bin directory is present in os.environ['PATH']
        bin_dir = str(Path(ffmpeg_bin).parent.resolve())
        paths = os.environ.get("PATH", "").split(os.pathsep)
        self.assertIn(bin_dir, paths, "Binary directory was not prepended to os.environ['PATH']")

    def test_02_command_diagnostics_success(self):
        """Verify run_command_diagnostics executes successfully on standard command."""
        res = run_command_diagnostics(["ffmpeg", "-version"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("ffmpeg version", res.stdout.lower() + res.stderr.lower())

    def test_03_command_diagnostics_failure_rich_output(self):
        """Verify run_command_diagnostics formats command, exit code, STDERR, and STDOUT on failure."""
        with self.assertRaises(RuntimeError) as ctx:
            run_command_diagnostics(["ffmpeg", "-invalid_argument_for_test"])

        err_msg = str(ctx.exception)
        self.assertIn("Command execution failed with exit code", err_msg)
        self.assertIn("Command:", err_msg)
        self.assertIn("STDERR:", err_msg)
        self.assertIn("STDOUT:", err_msg)

    def test_04_validate_media_file(self):
        """Verify media validation on valid file, zero-byte file, and missing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            missing_file = tmp_path / "missing.mp4"
            with self.assertRaises(RuntimeError) as ctx_missing:
                validate_media_file(missing_file)
            self.assertIn("file not found", str(ctx_missing.exception))

            empty_file = tmp_path / "empty.mp4"
            empty_file.write_bytes(b"")
            with self.assertRaises(RuntimeError) as ctx_empty:
                validate_media_file(empty_file)
            self.assertIn("zero bytes", str(ctx_empty.exception))

            invalid_content = tmp_path / "garbage.mp4"
            invalid_content.write_bytes(b"This is not a real video container")
            with self.assertRaises(RuntimeError) as ctx_invalid:
                validate_media_file(invalid_content)
            self.assertIn("ffprobe validation failed", str(ctx_invalid.exception))

    def test_05_stage8_recovery_scene_preservation(self):
        """Verify that Stage 8 detects existing scene MP4s and skips Manim execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            media_dir = session_dir / "media" / "videos" / "script" / "480p15"
            media_dir.mkdir(parents=True, exist_ok=True)

            scene_ids = ["Scene1_Test", "Scene2_Test"]
            # Generate a 1-second synthetic video for each scene using ffmpeg
            ffmpeg_bin = get_ffmpeg_path()
            for sid in scene_ids:
                scene_file = media_dir / f"{sid}.mp4"
                cmd = [
                    ffmpeg_bin, "-y",
                    "-f", "lavfi",
                    "-i", "color=c=blue:s=320x240:d=1.0:r=15",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    str(scene_file),
                ]
                run_command_diagnostics(cmd)
                self.assertTrue(scene_file.exists())
                self.assertGreater(scene_file.stat().st_size, 1000)

            # Generate synthetic voiceover audio (2 seconds silence/tone)
            voiceover = session_dir / "voiceover.mp3"
            audio_cmd = [
                ffmpeg_bin, "-y",
                "-f", "lavfi",
                "-i", "sine=frequency=440:duration=2.0",
                "-c:a", "libmp3lame",
                str(voiceover),
            ]
            run_command_diagnostics(audio_cmd)
            self.assertTrue(voiceover.exists())

            # Fake script.py
            script_py = session_dir / "script.py"
            script_py.write_text("# dummy script", encoding="utf-8")

            # Orchestrator instance
            orchestrator = VideoProductionOrchestrator(
                session_dir=session_dir,
                auto_approve=True,
            )
            orchestrator.artifacts.manim_script_py = str(script_py)
            orchestrator.artifacts.voiceover_mp3 = str(voiceover)

            timeline = [
                SceneTiming(
                    scene_id="Scene1_Test",
                    segment_index=0,
                    narration_text="Scene 1 test",
                    audio_start_s=0.0,
                    audio_end_s=1.0,
                    duration_s=1.0,
                    anim_duration_s=0.8,
                    hold_duration_s=0.2,
                    visual_elements=["box"],
                ),
                SceneTiming(
                    scene_id="Scene2_Test",
                    segment_index=1,
                    narration_text="Scene 2 test",
                    audio_start_s=1.0,
                    audio_end_s=2.0,
                    duration_s=1.0,
                    anim_duration_s=0.8,
                    hold_duration_s=0.2,
                    visual_elements=["arrow"],
                ),
            ]

            # Stage 8 should detect existing scene MP4s, concatenate without calling manim, and produce silent and synced videos
            probe = orchestrator.stage8_render_manim(timeline)

            silent_video = session_dir / "silent_video.mp4"
            synced_video = session_dir / "synced_video.mp4"
            concat_txt = session_dir / "concat.txt"

            self.assertTrue(silent_video.exists())
            self.assertTrue(synced_video.exists())
            self.assertTrue(concat_txt.exists())

            val_silent = validate_media_file(silent_video, min_duration=1.5)
            self.assertGreaterEqual(val_silent["duration_s"], 1.8)

            val_synced = validate_media_file(synced_video, min_duration=1.5)
            self.assertEqual(val_synced["video_streams"], 1)
            self.assertEqual(val_synced["audio_streams"], 1)

    def test_06_interrupted_ffmpeg_partial_file_cleanup(self):
        """Verify that a failed/interrupted loudnorm pass 2 does not leave corrupted partial files on disk."""
        from nexus_agent.compositor.loudnorm import TwoPassLoudnorm
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            in_wav = tmp_path / "input.wav"
            out_wav = tmp_path / "output.wav"

            # Create synthetic input WAV
            ffmpeg_bin = get_ffmpeg_path()
            run_command_diagnostics([
                ffmpeg_bin, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
                "-ar", "48000", "-c:a", "pcm_s16le", str(in_wav)
            ])

            # Bogus metrics that will cause loudnorm filter syntax error
            bogus_metrics = {
                "input_i": "-999.0",
                "input_tp": "invalid",
                "input_lra": "invalid",
                "input_thresh": "-999.0",
                "target_offset": "0.0"
            }

            with self.assertRaises(RuntimeError):
                TwoPassLoudnorm.apply_second_pass(in_wav, out_wav, bogus_metrics)

            # Ensure neither output.wav nor output.part.wav exists
            self.assertFalse(out_wav.exists(), "Corrupted output.wav was not cleaned up!")
            self.assertFalse(tmp_path.joinpath("output.part.wav").exists(), "Partial file was not cleaned up!")
            print("  [PASS] Interrupted/failed loudnorm pass cleanly removed partial files.")

    def test_07_stage10_recovery_with_existing_broadcast_clean(self):
        """Verify that Stage 10 reuses existing clean broadcast video and burns subtitles directly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir)
            broadcast_clean = session_dir / "broadcast.mp4"
            broadcast_sub = session_dir / "broadcast_subtitled.mp4"
            srt_file = session_dir / "subtitles.srt"
            timeline_json = session_dir / "scene_timeline.json"
            master_wav = session_dir / "audio_master.wav"

            ffmpeg_bin = get_ffmpeg_path()
            # Generate 2-second synthetic broadcast video
            run_command_diagnostics([
                ffmpeg_bin, "-y",
                "-f", "lavfi", "-i", "color=c=black:s=480x270:d=2.0:r=15",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
                "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k",
                str(broadcast_clean)
            ])

            # Write master audio WAV
            run_command_diagnostics([
                ffmpeg_bin, "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
                "-ar", "48000", "-c:a", "pcm_s16le", str(master_wav)
            ])

            # Write minimal timeline and subtitles
            timeline_json.write_text('{"scenes": [{"scene_id": "S1", "audio_start_s": 0.0, "audio_end_s": 2.0, "duration_s": 2.0, "narration_text": "Recovery test"}]}', encoding="utf-8")
            srt_file.write_text("1\n00:00:00,000 --> 00:00:02,000\nRecovery test\n", encoding="utf-8")

            orchestrator = VideoProductionOrchestrator(session_dir=session_dir, auto_approve=True)
            orchestrator.artifacts.scene_timeline_json = str(timeline_json)
            orchestrator.artifacts.master_wav = str(master_wav)
            orchestrator.artifacts.synced_video_mp4 = str(broadcast_clean)

            # Stage 10 should detect existing broadcast.mp4 and burn subtitles directly
            post_res = orchestrator.stage10_post_production()

            self.assertTrue(broadcast_sub.exists())
            sub_meta = validate_media_file(broadcast_sub, min_duration=1.8)
            self.assertEqual(sub_meta["video_streams"], 1)
            self.assertEqual(sub_meta["audio_streams"], 1)
            print("  [PASS] Stage 10 recovery cleanly reused broadcast.mp4 and burned subtitles.")

    def test_08_pipeline_master_audio_reuse(self):
        """Verify PostProductionPipeline reuses existing master audio without repeating sound design."""
        from nexus_agent.compositor.pipeline import PostProductionPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            video_src = temp_path / "synced.mp4"
            master_wav = temp_path / "audio_master.wav"
            timeline_json = temp_path / "timeline.json"
            out_video = temp_path / "output.mp4"

            ffmpeg_bin = get_ffmpeg_path()
            # 2.0-second video
            run_command_diagnostics([
                ffmpeg_bin, "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:d=2.0:r=15",
                "-c:v", "libx264", str(video_src)
            ])
            # 2.0-second master audio
            run_command_diagnostics([
                ffmpeg_bin, "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
                "-ar", "48000", "-c:a", "pcm_s16le", str(master_wav)
            ])
            # Voiceover source file
            vo_path = temp_path / "dummy.mp3"
            run_command_diagnostics([
                ffmpeg_bin, "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
                "-c:a", "libmp3lame", str(vo_path)
            ])
            timeline_json.write_text('{"scenes": []}', encoding="utf-8")

            pipeline = PostProductionPipeline(
                timeline_path=timeline_json,
                video_source=video_src,
                voiceover_source=vo_path,
            )
            res = pipeline.produce_broadcast_master(
                output_video=out_video,
                temp_dir=temp_path / "tmp_work",
                master_wav_path=master_wav,
            )
            self.assertTrue(out_video.exists())
            self.assertEqual(res["master_wav"], str(master_wav))
            print("  [PASS] Master audio reuse verified in PostProductionPipeline.")


if __name__ == "__main__":
    unittest.main()

