from bisect import bisect_right
from dataclasses import dataclass
from collections.abc import Iterator

from .cache import VideoFile
from .media import FRAME_BYTES


@dataclass(frozen=True)
class VisualFeature:
    video: VideoFile
    duration: float
    frames: bytes
    usable: tuple[bool, ...]

    @classmethod
    def build(cls, video: VideoFile, duration: float, frames: bytes) -> "VisualFeature":
        usable = tuple(
            sum(pixel <= 32 for pixel in frames[offset:offset + FRAME_BYTES]) * 5 < FRAME_BYTES * 4
            for offset in range(0, len(frames), FRAME_BYTES)
        )
        return cls(video, duration, frames, usable)


def compare(left: VisualFeature, right: VisualFeature, threshold: float) -> tuple[float, int] | None:
    positions = [i for i, (a, b) in enumerate(zip(left.usable, right.usable)) if a and b]
    if len(positions) < (len(left.usable) + 1) // 2:
        return None
    denominator = len(positions) * FRAME_BYTES * 255
    difference = 0
    left_pixels = memoryview(left.frames)
    right_pixels = memoryview(right.frames)
    for index in positions:
        frame = slice(index * FRAME_BYTES, (index + 1) * FRAME_BYTES)
        difference += sum(abs(a - b) for a, b in zip(left_pixels[frame], right_pixels[frame]))
        if difference / denominator > 1 - threshold + 1e-12:
            return None
    return 1 - difference / denominator, len(positions)


def duration_candidates(
    features: list[VisualFeature], ratio: float, seconds: float,
) -> Iterator[tuple[VisualFeature, VisualFeature]]:
    ordered = sorted(features, key=lambda feature: feature.duration)
    durations = [feature.duration for feature in ordered]
    for index, left in enumerate(ordered):
        end = bisect_right(durations, left.duration + max(seconds, left.duration * ratio))
        for other_index in range(index + 1, end):
            yield left, ordered[other_index]
