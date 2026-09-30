"""
TTS wrapper — gTTS → MP3 → ffmpeg encode → AAC (16kHz mono)
"""
import os
import subprocess
import tempfile
import time


def text_to_aac(text: str, lang: str = "vi") -> str:
    """
    Chuyển text thành file AAC tạm thời.
    Trả về path file AAC. Caller chịu trách nhiệm xóa file.
    Raise RuntimeError nếu thất bại.
    """
    from gtts import gTTS, gTTSError

    ts = str(int(time.time() * 1000))
    tmp_dir = "/tmp/imou_tts"
    os.makedirs(tmp_dir, exist_ok=True)

    mp3_path = os.path.join(tmp_dir, f"tts_{ts}.mp3")
    aac_path = os.path.join(tmp_dir, f"tts_{ts}.aac")

    try:
        # Bước 1: gTTS → MP3
        tts = gTTS(text=text, lang=lang)
        tts.save(mp3_path)
    except gTTSError as e:
        raise RuntimeError(f"gTTS lỗi: {e}")

    try:
        # Bước 2: ffmpeg → AAC 16kHz mono
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", mp3_path,
                "-ar", "16000",
                "-ac", "1",
                "-c:a", "aac",
                aac_path,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg lỗi: {result.stderr.decode()[:200]}")
    finally:
        # Xóa MP3 tạm
        if os.path.exists(mp3_path):
            os.unlink(mp3_path)

    return aac_path


def cleanup(path: str):
    """Xóa file tạm sau khi dùng xong."""
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass
