"""异步任务队列：批量 ffmpeg 任务并发受限执行 + 进度查询。"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "failed"  # 兼容占位，当前不实现取消


@dataclass
class Task:
    id: str
    kind: str                      # cut / rotate
    title: str
    source: str
    output: str = ""
    sources: list = field(default_factory=list)  # 多源文件任务（如拼接）
    status: str = TaskStatus.QUEUED
    progress: float = 0.0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None


class TaskQueue:
    def __init__(self, workers: int = 2):
        self._tasks: dict = {}
        self._sem = asyncio.Semaphore(workers)

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list(self) -> list:
        # 最新在前；超过 200 条时清理一周前已完成任务
        if len(self._tasks) > 200:
            cutoff = time.time() - 7 * 86400
            for tid in [k for k, v in self._tasks.items()
                        if v.finished_at and v.finished_at < cutoff]:
                del self._tasks[tid]
        return sorted(self._tasks.values(),
                      key=lambda t: -t.created_at)[:100]

    def clear_finished(self) -> int:
        """清除已完成/失败的任务记录（运行中的任务保留），返回清除数量。"""
        ids = [k for k, v in self._tasks.items() if v.finished_at is not None]
        for tid in ids:
            del self._tasks[tid]
        return len(ids)

    async def submit(self, kind: str, title: str, source: str, output: str,
                     job: Callable, sources: list = None) -> Task:
        """job: async callable(on_progress) -> None"""
        task = Task(id=uuid.uuid4().hex[:12], kind=kind, title=title,
                    source=source, output=output, sources=sources or [])
        self._tasks[task.id] = task
        asyncio.create_task(self._run(task, job))
        return task

    async def _run(self, task: Task, job: Callable):
        async with self._sem:
            task.status = TaskStatus.RUNNING
            try:
                await job(self._set_progress(task))
                task.status = TaskStatus.DONE
                task.progress = 1.0
            except asyncio.CancelledError:
                task.status = TaskStatus.FAILED
                task.error = "任务被取消"
            except Exception as e:  # noqa: BLE001
                task.status = TaskStatus.FAILED
                task.error = str(e)[:300]
            finally:
                task.finished_at = time.time()

    @staticmethod
    def _set_progress(task: Task):
        def cb(pct: float):
            task.progress = pct
        return cb
