"""
Imou Talkback — Flask server v1.3.0
Endpoints:
  GET  /              → Web UI
  POST /speak         → { text, camera } TTS ngắn
  POST /play_audio    → binary audio từ HA media_player
  POST /api/stop      → { camera } dừng phát
  POST /api/pause     → { camera } tạm dừng
  POST /api/resume    → { camera } phát tiếp
  POST /api/skip      → { camera } bỏ qua bài hiện tại
  POST /api/volume    → { camera, volume: 0.0-1.0 }
  GET  /api/status    → trạng thái tất cả camera
  GET  /api/key       → api_key
  GET  /api/cameras   → danh sách camera
  POST /api/cameras   → lưu danh sách camera
Auth: header X-API-Key (bỏ qua cho GET /)
"""
import json
import os
import threading
from functools import wraps
from flask import Flask, request, jsonify, render_template

import tts as tts_mod
import talkback
from queue_manager import QueueManager, QueueItem

app = Flask(__name__, template_folder="templates")

DATA_DIR = "/data"
KEY_FILE = os.path.join(DATA_DIR, "api_key.txt")
CAM_FILE = os.path.join(DATA_DIR, "cameras.json")
TTS_LANG = os.environ.get("TTS_LANG", "vi")

# ── Queue Manager ─────────────────────────────────────────────────────────────

def _on_state_change(cam_name: str, state):
    app.logger.info("Camera %s → %s", cam_name, state.value)

queue_manager = QueueManager(on_state_change=_on_state_change)

# ── helpers ───────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    with open(KEY_FILE) as f:
        return f.read().strip()

def _load_cameras() -> list:
    if os.path.exists(CAM_FILE):
        with open(CAM_FILE) as f:
            return json.load(f)
    options_path = "/data/options.json"
    if os.path.exists(options_path):
        with open(options_path) as f:
            opts = json.load(f)
        cams = opts.get("cameras", [])
        if cams:
            _save_cameras(cams)
            return cams
    return []

def _save_cameras(cameras: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CAM_FILE, "w") as f:
        json.dump(cameras, f, indent=2, ensure_ascii=False)

def _find_camera(name: str) -> dict | None:
    norm = name.strip().lower()
    for cam in _load_cameras():
        if cam["name"].strip().lower() == norm:
            return cam
    return None

# ── auth ──────────────────────────────────────────────────────────────────────

def require_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.headers.get("X-API-Key", "") != _get_api_key():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Web UI ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    api_key = _get_api_key()
    cameras = _load_cameras()
    yaml_snippet = (
        "media_player:\n"
        "  - platform: imou_talkback\n"
        "    name: imou_talkback\n"
        "    host: homeassistant.local\n"
        f"    port: 8765\n"
        f'    api_key: "{api_key}"'
    )
    return render_template(
        "index.html",
        api_key=api_key,
        cameras=cameras,
        yaml_snippet=yaml_snippet,
        tts_lang=TTS_LANG,
    )

# ── TTS ngắn (notify platform) ────────────────────────────────────────────────

@app.route("/speak", methods=["POST"])
@require_key
def speak():
    data     = request.get_json(force=True) or {}
    text     = data.get("text", "").strip()
    cam_name = data.get("camera", "")

    if not text:
        return jsonify({"error": "Thiếu text"}), 400

    cameras = _load_cameras()
    if not cameras:
        return jsonify({"error": "Chưa có camera nào"}), 400

    cam = _find_camera(cam_name) if cam_name else cameras[0]
    if not cam:
        return jsonify({"error": f"Không tìm thấy camera: {cam_name}"}), 404

    def _run():
        aac_path = None
        try:
            aac_path = tts_mod.text_to_aac(text, lang=TTS_LANG)
            talkback.push_audio(
                host=cam["host"],
                password=cam["password"],
                aac_path=aac_path,
                port=int(cam.get("port", 8086)),
                codec="aac-adts",
            )
        except Exception as e:
            app.logger.error("speak lỗi: %s", e)
        finally:
            tts_mod.cleanup(aac_path)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "camera": cam["name"], "text": text})

# ── Media Player endpoints ────────────────────────────────────────────────────

@app.route("/play_audio", methods=["POST"])
@require_key
def play_audio():
    """
    Nhận audio URL từ HA media_player → thêm vào queue camera.
    Header: X-Camera-Name, X-Media-URL
    Hoặc Body JSON: { camera, url, title }
    """
    # Hỗ trợ cả JSON body và header
    data = request.get_json(force=True, silent=True) or {}
    cam_name  = data.get("camera") or request.headers.get("X-Camera-Name", "")
    media_url = data.get("url")    or request.headers.get("X-Media-URL", "")

    if not media_url:
        # Fallback: nhận binary audio trực tiếp
        return _play_binary_audio(cam_name)

    cameras = _load_cameras()
    if not cameras:
        return jsonify({"error": "Chưa có camera nào"}), 400

    cam = _find_camera(cam_name) if cam_name else cameras[0]
    if not cam:
        return jsonify({"error": f"Không tìm thấy camera: {cam_name}"}), 404

    worker = queue_manager.get_or_create(cam)
    worker.enqueue(QueueItem(
        url=media_url,
        title=data.get("title", ""),
    ))

    return jsonify({"ok": True, "camera": cam["name"], "queued": media_url[:80]})


def _play_binary_audio(cam_name: str):
    """Nhận binary audio, lưu tạm, thêm vào queue."""
    import tempfile
    audio_data = request.get_data()
    if not audio_data:
        return jsonify({"error": "Không có audio data"}), 400

    cameras = _load_cameras()
    if not cameras:
        return jsonify({"error": "Chưa có camera nào"}), 400

    cam = _find_camera(cam_name) if cam_name else cameras[0]
    if not cam:
        return jsonify({"error": f"Không tìm thấy camera: {cam_name}"}), 404

    # Lưu binary vào file tạm, dùng file:// URL
    tmp = tempfile.NamedTemporaryFile(
        suffix=".audio", dir="/tmp", delete=False
    )
    tmp.write(audio_data)
    tmp.close()

    worker = queue_manager.get_or_create(cam)
    worker.enqueue(QueueItem(url=f"file://{tmp.name}"))

    return jsonify({"ok": True, "camera": cam["name"]})


@app.route("/api/stop", methods=["POST"])
@require_key
def api_stop():
    data     = request.get_json(force=True) or {}
    cam_name = data.get("camera", "")
    cam      = _find_camera(cam_name) if cam_name else (_load_cameras() or [None])[0]
    if not cam:
        return jsonify({"error": "Không tìm thấy camera"}), 404
    worker = queue_manager.get(cam["name"])
    if worker:
        worker.stop()
    return jsonify({"ok": True})


@app.route("/api/pause", methods=["POST"])
@require_key
def api_pause():
    data     = request.get_json(force=True) or {}
    cam_name = data.get("camera", "")
    cam      = _find_camera(cam_name) if cam_name else (_load_cameras() or [None])[0]
    if not cam:
        return jsonify({"error": "Không tìm thấy camera"}), 404
    worker = queue_manager.get(cam["name"])
    if worker:
        worker.pause()
    return jsonify({"ok": True})


@app.route("/api/resume", methods=["POST"])
@require_key
def api_resume():
    data     = request.get_json(force=True) or {}
    cam_name = data.get("camera", "")
    cam      = _find_camera(cam_name) if cam_name else (_load_cameras() or [None])[0]
    if not cam:
        return jsonify({"error": "Không tìm thấy camera"}), 404
    worker = queue_manager.get(cam["name"])
    if worker:
        worker.resume()
    return jsonify({"ok": True})


@app.route("/api/skip", methods=["POST"])
@require_key
def api_skip():
    data     = request.get_json(force=True) or {}
    cam_name = data.get("camera", "")
    cam      = _find_camera(cam_name) if cam_name else (_load_cameras() or [None])[0]
    if not cam:
        return jsonify({"error": "Không tìm thấy camera"}), 404
    worker = queue_manager.get(cam["name"])
    if worker:
        worker.skip()
    return jsonify({"ok": True})


@app.route("/api/volume", methods=["POST"])
@require_key
def api_volume():
    data     = request.get_json(force=True) or {}
    cam_name = data.get("camera", "")
    volume   = float(data.get("volume", 1.0))
    cam      = _find_camera(cam_name) if cam_name else (_load_cameras() or [None])[0]
    if not cam:
        return jsonify({"error": "Không tìm thấy camera"}), 404
    worker = queue_manager.get_or_create(cam)
    worker.set_volume(volume)
    return jsonify({"ok": True, "volume": volume})

# ── Status & Config ───────────────────────────────────────────────────────────

@app.route("/api/status")
@require_key
def api_status():
    cameras = _load_cameras()
    states  = queue_manager.all_states()
    result  = []
    for cam in cameras:
        worker = queue_manager.get(cam["name"])
        result.append({
            "name":   cam["name"],
            "host":   cam["host"],
            "port":   cam.get("port", 8086),
            "state":  states.get(cam["name"], "idle"),
            "volume": worker.volume if worker else 1.0,
        })
    return jsonify(result)


@app.route("/api/key")
@require_key
def api_key():
    return jsonify({"api_key": _get_api_key()})


@app.route("/api/cameras", methods=["GET"])
@require_key
def get_cameras():
    return jsonify(_load_cameras())


@app.route("/api/cameras", methods=["POST"])
@require_key
def save_cameras():
    cameras = request.get_json(force=True)
    if not isinstance(cameras, list):
        return jsonify({"error": "Cần array JSON"}), 400
    for cam in cameras:
        if not cam.get("name") or not cam.get("host") or not cam.get("password"):
            return jsonify({"error": "Mỗi camera cần: name, host, password"}), 400
    _save_cameras(cameras)
    return jsonify({"ok": True, "count": len(cameras)})

# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=False)
