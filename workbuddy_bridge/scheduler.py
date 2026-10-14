"""Bounded, lazy worker pool: queued jobs do not consume a thread or runtime."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable


class Scheduler:
    def __init__(self, workers: int = 4, capacity: int = 32) -> None:
        if workers < 1 or capacity < 1:
            raise ValueError("workers and capacity must be positive")
        self.workers = workers
        self.capacity = capacity
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue(capacity)
        self._lock = threading.Lock()
        self._started = False

    def submit(self, job: Callable[[], None]) -> bool:
        with self._lock:
            if not self._started:
                for index in range(self.workers):
                    threading.Thread(target=self._work, name=f"wb-worker-{index}", daemon=True).start()
                self._started = True
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                return False
        return True

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            try:
                job()
            except Exception:
                logging.getLogger(__name__).exception("Worker job failed")
            finally:
                self._queue.task_done()

    @property
    def queued(self) -> int:
        return self._queue.qsize()
