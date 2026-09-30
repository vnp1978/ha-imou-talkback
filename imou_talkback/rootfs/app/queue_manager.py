"""
Queue Manager — quản lý hàng đợi phát nhạc cho từng camera.
Mỗi camera có 1 QueueWorker riêng với thread riêng.
"""
import os
import logging
import subprocess
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from queue import Queue, Empty
from typing import Callable, Optional

import talkback

logger = logging.getLogger(__name__)


class CameraState(str, Enum):
    IDLE      = "idle"
    PLAYING   = "playing"
    PAUSED    = "paused"
    UNAVAILABLE = "unavailable"


@dataclass
class QueueItem:
    url: str
    content_type: str = "audio"
    title: str = ""


class QueueWorker:
    """Worker thread xử lý queue phát nhạc cho 1 camera."""

    def __init__(self, cam: dict, on_state_change: Callable):
        self._cam   = cam
        self._on_state_change = on_state_change
        self._queue: Queue = Queue()
        self._state = CameraState.IDLE
        self._volume = 1.0          # 0.0 - 2.0
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()
        self._skip_event  = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._current_proc: Optional[subprocess.Popen] = None

    # ── Public API ────────────────────────────────────────────────

    def enqueue(self, item: QueueItem):
        self._queue.put(item)
        if self._state == CameraState.IDLE:
            self._stop_event.clear()
            self._pause_event.clear()

    def stop(self):
        """Dừng phát, xóa queue."""
        self._stop_event.set()
        self._kill_current()
        with self._queue.mutex:
            self._queue.queue.clear()
        self._set_state(CameraState.IDLE)

    def pause(self):
        """Tạm dừng — đóng TCP connection, giữ queue."""
        if self._state == CameraState.PLAYING:
            self._pause_event.set()
            self._kill_current()
            self._set_state(CameraState.PAUSED)

    def resume(self):
        """Phát tiếp từ bài kế trong queue (camera không support seek)."""
        if self._state == CameraState.PAUSED:
            self._pause_event.clear()
            self._stop_event.clear()
            self._set_state(CameraState.PLAYING)

    def skip(self):
        """Bỏ qua bài hiện tại."""
        self._skip_event.set()
        self._kill_current()

    def set_volume(self, volume: float):
        """volume: 0.0 - 1.0 từ HA, map sang 0.0 - 2.0 cho ffmpeg."""
        self._volume = max(0.0, min(2.0, volume * 2))

    @property
    def state(self) -> CameraState:
        return self._state

    @property
    def volume(self) -> float:
        return self._volume / 2.0  # trả về 0.0-1.0 cho HA

    # ── Internal ──────────────────────────────────────────────────

    def _set_state(self, state: CameraState):
        if self._state != state:
            self._state = state
            self._on_state_change(self._cam["name"], state)

    def _kill_current(self):
        proc = self._current_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except: pass
        self._current_proc = None

    def _run(self):
        """Main worker loop."""
        while True:
            # Chờ item tiếp theo
            try:
                item = self._queue.get(timeout=1)
            except Empty:
                if self._state == CameraState.PLAYING:
                    self._set_state(CameraState.IDLE)
                continue

            if self._stop_event.is_set():
                self._queue.task_done()
                continue

            if self._pause_event.is_set():
                # Đưa item trở lại queue đầu
                new_q: Queue = Queue()
                new_q.put(item)
                while not self._queue.empty():
                    try: new_q.put(self._queue.get_nowait())
                    except Empty: break
                self._queue = new_q
                time.sleep(0.5)
                continue

            self._skip_event.clear()
            self._set_state(CameraState.PLAYING)
            self._play_item(item)
            self._queue.task_done()

            # Hết queue → idle
            if self._queue.empty() and not self._stop_event.is_set():
                self._set_state(CameraState.IDLE)

    def _play_item(self, item: QueueItem):
        """Download → convert → push audio cho 1 item."""
        tmp_raw  = None
        tmp_audio = None
        try:
            # Download audio
            tmp_raw = tempfile.NamedTemporaryFile(
                suffix=".raw_audio", dir="/tmp", delete=False
            )
            tmp_raw.close()

            logger.info("Downloading: %s", item.url[:80])
            urllib.request.urlretrieve(item.url, tmp_raw.name)

            # Convert sang AAC 16kHz mono
            use_codec = "aac-adts"
            tmp_audio = tmp_raw.name + ".aac"

            vol_filter = f"volume={self._volume}"
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", tmp_raw.name,
                    "-af", vol_filter,
                    "-ar", "16000",
                    "-ac", "1",
                    "-c:a", "aac",
                    tmp_audio,
                ],
                capture_output=True,
                timeout=120,
            )

            if result.returncode != 0:
                logger.error(
                    "ffmpeg lỗi: %s",
                    result.stderr.decode()[:300]
                )
                return

            if self._skip_event.is_set() or self._stop_event.is_set():
                return

            # Push qua visualtalk
            talkback.push_audio(
                host=self._cam["host"],
                password=self._cam["password"],
                aac_path=tmp_audio,
                port=int(self._cam.get("port", 8086)),
                codec=use_codec,
                volume=self._volume,
            )

        except Exception as e:
            logger.error(
                "play_item lỗi cam=%s: %s",
                self._cam["name"], e
            )
        finally:
            for f in [tmp_raw.name if tmp_raw else None, tmp_audio]:
                if f and os.path.exists(f):
                    try: os.unlink(f)
                    except: pass


class QueueManager:
    """Quản lý tất cả QueueWorker theo tên camera."""

    def __init__(self, on_state_change: Callable):
        self._workers: dict[str, QueueWorker] = {}
        self._on_state_change = on_state_change
        self._lock = threading.Lock()

    def get_or_create(self, cam: dict) -> QueueWorker:
        name = cam["name"]
        with self._lock:
            if name not in self._workers:
                self._workers[name] = QueueWorker(cam, self._on_state_change)
            return self._workers[name]

    def get(self, name: str) -> Optional[QueueWorker]:
        return self._workers.get(name)

    def remove(self, name: str):
        worker = self._workers.pop(name, None)
        if worker:
            worker.stop()

    def all_states(self) -> dict[str, str]:
        return {
            name: worker.state.value
            for name, worker in self._workers.items()
        }
