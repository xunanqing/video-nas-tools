import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from app.dedup.cache import VideoFile
from app.dedup.matching import VisualFeature, compare, duration_candidates
from app.dedup.media import FFMPEG_BIN, FFPROBE_BIN, FRAME_BYTES, sample_times


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.video = VideoFile(Path("test.mp4"), 0, 0, 0)

    def feature(self, duration, *levels):
        frames = b"".join(bytes([level]) * FRAME_BYTES for level in levels)
        return VisualFeature.build(self.video, duration, frames)

    def test_sample_positions_and_inclusive_threshold(self):
        self.assertEqual(sample_times(12, 3), [3, 6, 9])
        left = self.feature(2, 0, 100, 100)
        right = self.feature(2, 31, 151, 100)
        self.assertEqual(compare(left, right, 0.9), (0.9, 2))
        self.assertIsNone(compare(left, right, 0.901))
        self.assertIsNone(compare(left, self.feature(2, 100, 0, 100), 0))

    def test_duration_candidates_respect_shorter_file_tolerance(self):
        shorter = self.feature(10, 100)
        at_boundary = self.feature(11, 100)
        longer = self.feature(11.05, 100)
        matches = list(duration_candidates([at_boundary, shorter, longer], 0.1, 0.25))
        self.assertEqual(matches, [(shorter, at_boundary), (at_boundary, longer)])


class ScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.media = self.workspace / "本地 视频"
        self.nested = self.media / "nested"
        self.nested.mkdir(parents=True)
        self.other_root = self.workspace / "另一目录"
        self.other_root.mkdir()
        self.cache = self.workspace / "cache.sqlite3"

    def run_scan(self, *options, expected_exit=0):
        proc = subprocess.run(
            [sys.executable, "-m", "app.dedup.cli", str(self.media), str(self.nested),
             str(self.other_root), "--cache", str(self.cache), *options],
            capture_output=True, timeout=120,
        )
        self.assertEqual(proc.returncode, expected_exit, proc.stderr.decode(errors="replace"))
        return [json.loads(line) for line in proc.stdout.splitlines()]

    def test_exact_scan_reuses_cache_and_invalidates_modified_file(self):
        source = self.media / "a.mp4"
        clone = self.nested / "b.mp4"
        source.write_bytes(b"copy")
        clone.write_bytes(b"copy")
        (self.nested / "c.mp4").write_bytes(b"diff")
        cold = self.run_scan("--exact-only")
        self.assertEqual([row["type"] for row in cold], ["exact", "summary"])
        self.assertEqual(set(map(Path, cold[0]["paths"])), {source.resolve(), clone.resolve()})
        self.assertEqual(cold[-1]["files"], 3)
        self.assertEqual(self.run_scan("--exact-only")[-1]["exact_cache_hits"], 3)

        previous = clone.stat()
        clone.write_bytes(b"new!")
        os.utime(clone, ns=(previous.st_atime_ns, previous.st_mtime_ns + 2_000_000_000))
        updated = self.run_scan("--exact-only")
        self.assertEqual([row["type"] for row in updated], ["summary"])
        self.assertEqual(updated[-1]["exact_cache_hits"], 2)
        self.assertEqual(self.run_scan("--exact-only", "--refresh")[-1]["exact_cache_hits"], 0)

    @unittest.skipUnless(shutil.which(FFMPEG_BIN) and shutil.which(FFPROBE_BIN), "需要 FFmpeg 和 FFprobe")
    def test_visual_scan_recognizes_reencoding_and_isolates_bad_input(self):
        source = self.media / "source.mp4"
        clone = self.nested / "完全相同.mp4"
        variant = self.other_root / "reencoded.mkv"
        negative = self.media / "different.avi"
        broken = self.media / "broken.mp4"

        def ffmpeg(*args):
            subprocess.run(
                [FFMPEG_BIN, "-nostdin", "-v", "error", *args],
                check=True, capture_output=True, timeout=30,
            )

        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=3", "-c:v", "mpeg4", str(source))
        shutil.copyfile(source, clone)
        ffmpeg("-i", str(source), "-vf", "scale=320:180", "-c:v", "ffv1", str(variant))
        ffmpeg("-f", "lavfi", "-i", "smptebars=size=160x90:rate=24:duration=3", "-c:v", "mpeg4", str(negative))
        broken.write_bytes(b"broken")
        originals = {path: path.read_bytes() for path in (source, clone, variant, negative, broken)}

        rows = self.run_scan(expected_exit=1)
        self.assertEqual(rows[-1]["errors"], 1)
        self.assertEqual(rows[-1]["exact_groups"], 1)
        pairs = {frozenset(Path(path).name for path in row["paths"]): row["similarity"]
                 for row in rows if row["type"] == "visual"}
        self.assertEqual(set(pairs), {frozenset((source.name, variant.name)), frozenset((clone.name, variant.name))})
        self.assertTrue(all(score >= 0.96 for score in pairs.values()))
        warm = self.run_scan(expected_exit=1)
        self.assertEqual(warm[-1]["visual_cache_hits"], 4)
        self.assertEqual(warm[:-1], rows[:-1])
        self.assertEqual(self.run_scan("--samples", "3", expected_exit=1)[-1]["visual_cache_hits"], 0)
        self.assertEqual({path: path.read_bytes() for path in originals}, originals)


if __name__ == "__main__":
    unittest.main()
