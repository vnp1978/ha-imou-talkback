"""
Imou Talkback — visualtalk pipeline
Gọi imou_visualtalk.py (từ imou-life repo) qua subprocess.
Script được COPY vào /opt/ khi build Docker image.
"""
import os
import subprocess
import logging

logger = logging.getLogger(__name__)

VISUALTALK_SCRIPT = "/opt/imou_visualtalk.py"
DEFAULT_PORT       = 8086
DEFAULT_USERNAME   = "admin"

# AAC 16kHz: mỗi frame = 1024 samples / 16000Hz = 64ms
# G.711 8kHz: mỗi frame = 160 samples / 8000Hz = 20ms
CODEC_FRAME_MS = {
    "aac-adts": 64,
    "aac-raw":  64,
    "alaw":     20,
    "mulaw":    20,
    "copy":     20,
}

# Timeout tối đa cho 1 bài nhạc (30 phút)
STREAM_TIMEOUT = 1800


def push_audio(
    host: str,
    password: str,
    aac_path: str,
    username: str = DEFAULT_USERNAME,
    port: int = DEFAULT_PORT,
    codec: str = "aac-adts",
    volume: float = 1.0,
):
    """
    Phát file audio qua loa camera Imou (LAN mode).
    Dùng imou_visualtalk.py với frame-ms đúng theo codec.
    volume: 0.0 - 2.0 (1.0 = bình thường)
    Raise RuntimeError nếu thất bại.
    """
    if not os.path.exists(VISUALTALK_SCRIPT):
        raise RuntimeError(
            f"Không tìm thấy {VISUALTALK_SCRIPT}. "
            "Kiểm tra lại Dockerfile đã COPY imou scripts chưa."
        )

    if not os.path.exists(aac_path):
        raise RuntimeError(f"File audio không tồn tại: {aac_path}")

    frame_ms = CODEC_FRAME_MS.get(codec, 20)

    cmd = [
        "python3", VISUALTALK_SCRIPT,
        host,
        "--port",      str(port),
        "--username",  username,
        "--password",  password,
        "--audio",     aac_path,
        "--codec",     codec,
        "--frame-ms",  str(frame_ms),
        "--timeout",   str(STREAM_TIMEOUT),
    ]

    logger.debug(
        "visualtalk cmd: %s",
        " ".join(cmd[:-1] + ["--password", "***"])
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=STREAM_TIMEOUT + 30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"visualtalk timeout sau {STREAM_TIMEOUT}s")
    except FileNotFoundError:
        raise RuntimeError("python3 không tìm thấy trong container")

    stdout = result.stdout.decode(errors="ignore").strip()
    stderr = result.stderr.decode(errors="ignore").strip()

    if result.returncode != 0:
        detail = stderr or stdout or "(no output)"
        raise RuntimeError(
            f"visualtalk thất bại (exit {result.returncode}): {detail[-600:]}"
        )

    logger.debug("visualtalk OK: %s", stdout[:200] if stdout else "(no output)")
