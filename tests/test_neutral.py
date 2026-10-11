from pathlib import Path
import unittest

from capture_app.models import Scene, Segment
from capture_app.neutral import NEUTRAL_TRIM_MS, make_neutral_scene, neutral_timing
from capture_app.session import SegmentTiming, SessionPaths, build_manifest_rows


class NeutralTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scene = Scene(
            question_set_id="QSET",
            question_set_name="Question set name",
            scene_id="scene001",
            ordinal=1,
            text="Formal scene",
            segments=(
                Segment("QSET", "scene001", "S001", 1, "Formal segment", "smile"),
            ),
            image_path=Path("formal_scene.png"),
            image_prompt="Formal image prompt",
            scenario_description="Formal scene description",
        )

    def test_neutral_scene_metadata(self) -> None:
        scene = make_neutral_scene(self.scene, 2)

        self.assertEqual(scene.question_set_id, self.scene.question_set_id)
        self.assertEqual(scene.question_set_name, self.scene.question_set_name)
        self.assertEqual(scene.scene_id, "neutral_after_scene001")
        self.assertEqual(scene.ordinal, 2)
        self.assertIn("Neutral expression during rest", scene.text)
        self.assertIsNone(scene.image_path)
        self.assertEqual(scene.image_prompt, "")
        self.assertEqual(scene.scenario_description, "")
        self.assertEqual(len(scene.segments), 1)

        segment = scene.segments[0]
        self.assertEqual(segment.question_set_id, self.scene.question_set_id)
        self.assertEqual(segment.scene_id, scene.scene_id)
        self.assertEqual(segment.segment_id, "N001")
        self.assertEqual(segment.ordinal, 1)
        self.assertEqual(segment.text, "Neutral expression during rest")
        self.assertEqual(segment.purpose, "neutral")
        self.assertEqual(segment.category, "neutral")
        self.assertEqual(segment.stimulus_format, "neutral")
        self.assertTrue(segment.neutral)
        self.assertFalse(segment.image_caption)
        self.assertFalse(segment.scene_dialogue)
        self.assertEqual(segment.instruction, "")
        self.assertEqual(segment.utterance, "")

    def test_timing_trims_exactly_three_seconds(self) -> None:
        self.assertEqual(NEUTRAL_TRIM_MS, 3000)
        for duration_ms in (3001, 10_000, 25_432):
            with self.subTest(duration_ms=duration_ms):
                timing = neutral_timing(duration_ms)
                self.assertEqual(timing.segment_id, "N001")
                self.assertEqual(timing.start_ms, 3000)
                self.assertEqual(timing.end_ms, duration_ms)
                self.assertEqual(timing.duration_ms, duration_ms - 3000)

    def test_short_recordings_have_no_valid_neutral_segment(self) -> None:
        for duration_ms in (-1, 0, 2999, 3000):
            with self.subTest(duration_ms=duration_ms):
                with self.assertRaisesRegex(ValueError, "No valid neutral segment"):
                    neutral_timing(duration_ms)

    def test_manifest_labels_trim_and_independent_clip_path(self) -> None:
        root = Path.cwd() / "neutral_test_session"
        paths = SessionPaths(
            root=root,
            practice=root / "practice",
            raw=root / "raw",
            clips=root / "clips",
            logs=root / "logs",
            manifest=root / "manifest.csv",
            session_json=root / "session.json",
        )
        neutral_scene = make_neutral_scene(self.scene, 2)
        neutral_rows = build_manifest_rows(
            participant_id="participant",
            session_paths=paths,
            scene=neutral_scene,
            scene_order=2,
            raw_video_path=paths.raw / "neutral_after_scene001.mp4",
            timings=[neutral_timing(10_000)],
        )
        formal_rows = build_manifest_rows(
            participant_id="participant",
            session_paths=paths,
            scene=self.scene,
            scene_order=1,
            raw_video_path=paths.raw / "scene001.mp4",
            timings=[SegmentTiming("S001", 0, 10_000)],
        )

        self.assertEqual(len(neutral_rows), 1)
        row = neutral_rows[0]
        self.assertEqual(row["question_set_id"], "QSET")
        self.assertEqual(row["question_set_name"], "Question set name")
        self.assertEqual(row["scene_id"], "neutral_after_scene001")
        self.assertEqual(row["scene_order"], 2)
        self.assertEqual(row["segment_id"], "N001")
        self.assertEqual(row["segment_order"], 1)
        self.assertEqual(row["stimulus_format"], "neutral")
        self.assertEqual(row["purpose"], "neutral")
        self.assertEqual(row["image_path"], "")
        self.assertEqual(row["instruction_text"], "")
        self.assertEqual(row["utterance_text"], "")
        self.assertEqual(row["start_ms"], 3000)
        self.assertEqual(row["end_ms"], 10_000)
        self.assertEqual(row["duration_ms"], 7000)
        self.assertEqual(row["raw_video_path"], "raw/neutral_after_scene001.mp4")
        self.assertEqual(
            row["clip_video_path"],
            "clips/002_QSET_neutral_after_scene001_N001.mp4",
        )
        self.assertNotEqual(row["clip_video_path"], formal_rows[0]["clip_video_path"])
        self.assertNotEqual(row["raw_video_path"], formal_rows[0]["raw_video_path"])


if __name__ == "__main__":
    unittest.main()
