import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoFile:
    path: Path
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int = 0
    inode: int = 0
    birthtime: float | None = None

    @classmethod
    def read(cls, path: Path) -> "VideoFile":
        stat = path.stat()
        return cls(path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                   stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime", None))

    def verify(self) -> None:
        if self != self.read(self.path):
            raise OSError("文件在扫描期间发生变化")


class FeatureCache:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        with db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS dedup_features (
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    ctime_ns INTEGER NOT NULL,
                    duration REAL,
                    data BLOB NOT NULL,
                    PRIMARY KEY (path, kind)
                )
            """)

    def get(self, video: VideoFile, kind: str) -> tuple[float | None, bytes] | None:
        return self.db.execute("""
            SELECT duration, data FROM dedup_features
            WHERE path = ? AND kind = ? AND size = ? AND mtime_ns = ? AND ctime_ns = ?
        """, (str(video.path), kind, video.size, video.mtime_ns, video.ctime_ns)).fetchone()

    def put(self, video: VideoFile, kind: str, data: bytes, duration: float | None = None) -> None:
        with self.db:
            self.db.execute("""
                INSERT INTO dedup_features (path, kind, size, mtime_ns, ctime_ns, duration, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (path, kind) DO UPDATE SET
                    size = excluded.size, mtime_ns = excluded.mtime_ns, ctime_ns = excluded.ctime_ns,
                    duration = excluded.duration, data = excluded.data
            """, (str(video.path), kind, video.size, video.mtime_ns, video.ctime_ns, duration, data))

    def invalidate(self, path: Path) -> None:
        with self.db:
            self.db.execute("DELETE FROM dedup_features WHERE path = ?", (str(path),))
