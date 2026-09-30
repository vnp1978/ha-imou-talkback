"""Nói ra loa camera Dahua/Imou qua cổng 37777 (giao thức NetSDK "CLIENT_StartTalkEx").

Python thuần, không phụ thuộc Home Assistant — để test được riêng và dùng lại.

Camera Imou (lõi Dahua) không có kênh ngược RTSP/ONVIF; đường còn lại là giao
thức nhị phân trên cổng 37777, đúng đường app Imou dùng để nói. Mô tả khung byte
theo go2rtc PR #2431 (``pkg/dahua/netsdk.go``). Đã chạy thật trên bốn camera Imou:
mở kênh nói 0,04–0,9 giây, camera tự tắt mic của nó trong lúc phát.
"""

from __future__ import annotations

import hashlib
import socket
import struct
import threading
import time

CONG = 37777
#: Camera một mắt chỉ có kênh 0. Số kênh ngoài dải làm một số firmware khởi động
#: lại liên tục (ghi trong PR #2431) — nên không mở cho người dùng chỉnh.
KENH = 0
TAN_SO = 8000
#: PCM16 mono 8 kHz, khối 40 ms — định dạng của bản Talk mẫu của Dahua.
KHOI = 640

_HDR = 32
_A0, _B0, _A1, _F4, _TALK = 0xA0, 0xB0, 0xA1, 0xF4, 0x1D
_THACH = bytes([0x05, 0x02, 0x00, 0x01, 0x00, 0x00, 0xA1, 0xAA])
_DANG_NHAP = bytes([0x05, 0x02, 0x00, 0x08, 0x00, 0x00, 0xA1, 0xAA])
LY_DO = {1: "wrong password", 2: "unknown user", 4: "user already logged in elsewhere",
         5: "account locked", 6: "blocked after too many failed logins", 7: "device busy",
         8: "no free connection", 9: "no free channel"}


class TalkError(RuntimeError):
    """Nói không được."""


class AuthError(TalkError):
    """Camera từ chối đăng nhập — KHÔNG thử lại (camera khoá phiên sau vài lần sai)."""


def _khung(cmd: int, than: bytes = b"", duoi: bytes = b"") -> bytes:
    h = bytearray(_HDR)
    h[0] = cmd
    if cmd == _A0:
        h[1:4] = b"\x05\x00\x60"
    h[4:8] = struct.pack("<I", len(than))
    if len(duoi) == 8:
        h[24:32] = duoi
    return bytes(h) + than


def khung_tieng(pcm: bytes) -> bytes:
    """Một khung âm thanh 0x1D: đầu 32 byte + đầu phụ DHAV (PCM16, 8 kHz) + dữ liệu."""
    h = bytearray(_HDR)
    h[0] = _TALK
    h[4:8] = struct.pack("<I", 8 + len(pcm))
    h[8] = 0x02
    h[9:21] = struct.pack("<III", 16, 1, TAN_SO)
    return bytes(h) + b"\x00\x00\x01\xF0" + bytes([0x0C, 2]) + struct.pack("<H", len(pcm)) + pcm


def _doc_du(s: socket.socket, n: int) -> bytes:
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("camera closed the connection")
        b += c
    return b


def doc_khung(s: socket.socket) -> tuple[bytes, bytes]:
    h = _doc_du(s, _HDR)
    n = struct.unpack("<I", h[4:8])[0]
    if n > 65536:
        raise ConnectionError(f"bogus frame length {n}")
    return h, (_doc_du(s, n) if n else b"")


def _kv(than: bytes) -> dict[str, str]:
    out = {}
    for d in than.decode(errors="replace").rstrip("\x00\r\n").split("\r\n"):
        if ":" in d:
            k, v = d.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _chu(s: socket.socket, dong: list[str]) -> None:
    s.sendall(_khung(_F4, ("\r\n".join(dong) + "\r\n\r\n").encode()))


def _cho_chu(s: socket.socket, muon: str) -> dict[str, str]:
    for _ in range(32):
        h, b = doc_khung(s)
        if h[0] == _F4 and muon.encode() in b:
            return _kv(b)
    raise ConnectionError(f"no {muon} reply")


# MD5 do giao thức Dahua bắt buộc để băm mật khẩu đăng nhập.
def _md5(x: str) -> str:
    return hashlib.md5(x.encode(), usedforsecurity=False).hexdigest().upper()


def _gen1(mk: str) -> str:
    d = hashlib.md5(mk.encode(), usedforsecurity=False).digest()
    ra = ""
    for i in range(8):
        v = (d[i * 2] + d[i * 2 + 1]) % 62
        ra += chr(v + 48 if v < 10 else v + 55 if v < 36 else v + 61)
    return ra


class TalkSession:
    """Một phiên nói: đăng nhập, mở kênh, phát, đóng. Dùng với ``with``. Chặn (blocking)."""

    #: ``send_pcm`` nhận PCM16 mono ở tần số này.
    tan_so = TAN_SO

    def __init__(self, host: str, username: str, password: str, *, port: int = CONG,
                 timeout: float = 5.0) -> None:
        self.host, self.username, self.password = host, username, password
        self.port, self.timeout = port, timeout
        self.ctrl: socket.socket | None = None
        self.sub: socket.socket | None = None
        self.session, self.connection_id = 0, ""
        self._dung = threading.Event()
        self._luong: list[threading.Thread] = []

    def __enter__(self) -> "TalkSession":
        try:
            self._mo()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def login(self) -> None:
        """Chỉ đăng nhập (dùng để kiểm tài khoản trong config flow)."""
        self.ctrl = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.ctrl.sendall(_khung(_A0, duoi=_THACH))
        h, b = doc_khung(self.ctrl)
        t = _kv(b)
        if h[0] != _B0 or not t.get("Realm") or not t.get("Random"):
            raise TalkError("device does not speak the Dahua talk protocol on this port")
        u, mk = self.username, self.password
        g2 = _md5(u + ":" + t["Random"] + ":" + _md5(u + ":" + t["Realm"] + ":" + mk))
        g1 = _md5(u + ":" + t["Random"] + ":" + _gen1(mk))
        self.ctrl.sendall(_khung(_A0, f"{u}&&{g2}{g1}".encode(), _DANG_NHAP))
        h, _b = doc_khung(self.ctrl)
        self.session = struct.unpack("<I", h[16:20])[0]
        if not self.session:
            raise AuthError(f"login refused: {LY_DO.get(h[8], f'code {h[8]}')}")

    def _mo(self) -> None:
        self.login()
        _chu(self.ctrl, ["TransactionID:6", "Method:AddObject",
                         "ParameterName:Dahua.Device.Network.ControlConnection.Passive",
                         "ConnectProtocol:0"])
        t = _cho_chu(self.ctrl, "AddObjectResponse")
        if t.get("FaultCode") not in ("OK", "", None) or not t.get("ConnectionID"):
            raise TalkError(f"AddObject failed ({t.get('FaultCode')})")
        self.connection_id = t["ConnectionID"]
        self.sub = socket.create_connection((self.host, self.port), timeout=self.timeout)
        _chu(self.sub, ["TransactionID:0", "Method:GetParameterNames",
                        "ParameterName:Dahua.Device.Network.ControlConnection.AckSubChannel",
                        f"SessionID:{self.session}", f"ConnectionID:{self.connection_id}",
                        "Encrypt:0"])
        if _cho_chu(self.sub, "AckSubChannel").get("FaultCode") != "OK":
            raise TalkError("sub channel not acknowledged")
        self.sub.sendall(_khung(_A1))
        self._trang_thai(True)
        for s in (self.ctrl, self.sub):
            s.settimeout(None)
            self._luong.append(threading.Thread(target=self._xa, args=(s,), name="dahua-talk-rx",
                                                daemon=True))
        self._luong.append(threading.Thread(target=self._giu, name="dahua-talk-keepalive",
                                            daemon=True))
        for t in self._luong:
            t.start()

    def _trang_thai(self, bat: bool) -> None:
        _chu(self.ctrl, ["TransactionID:" + ("7" if bat else "8"), "Method:GetParameterNames",
                         "ParameterName:Dahua.Device.Network.Talk.General",
                         f"Channel:{KENH}", "EncodeFormat:1",
                         "Depth:" + ("16" if bat else "0"),
                         "Frequency:" + (str(TAN_SO) if bat else "0"),
                         "State:" + ("1" if bat else "0"),
                         f"ConnectionID:{self.connection_id}", "TalkMode:0"])

    def _xa(self, s: socket.socket) -> None:
        try:
            while not self._dung.is_set():
                doc_khung(s)
        except (OSError, ConnectionError):
            pass

    def _giu(self) -> None:
        while not self._dung.wait(1.0):
            try:
                self.ctrl.sendall(_khung(_A1))
            except OSError:
                return

    def send_pcm(self, pcm: bytes) -> None:
        """PCM16 LE mono 8 kHz, gửi từng khối 40 ms đúng nhịp thời gian thực."""
        t0 = time.monotonic()
        for i in range(0, len(pcm), KHOI):
            self.sub.sendall(khung_tieng(pcm[i:i + KHOI]))
            cho = t0 + (i + KHOI) / (2 * TAN_SO) - time.monotonic()
            if cho > 0:
                time.sleep(cho)

    def close(self) -> None:
        self._dung.set()
        try:
            if self.ctrl is not None and self.connection_id:
                self._trang_thai(False)
                _chu(self.ctrl, ["TransactionID:9", "Method:DeleteObject",
                                 "ParameterName:Dahua.Device.Network.ControlConnection.Passive",
                                 f"ConnectionID:{self.connection_id}"])
        except OSError:
            pass
        for s in (self.sub, self.ctrl):
            if s is not None:
                # shutdown đánh thức luồng đang chặn ở recv(); chỉ close() thì luồng
                # ấy kẹt tới khi camera tự đóng — mỗi lần phát rò một luồng.
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                s.close()
        self.sub = self.ctrl = None
        for t in self._luong:
            if t is not threading.current_thread():
                t.join(2)


def check_login(host: str, username: str, password: str, port: int = CONG) -> None:
    """Đăng nhập thử rồi đóng. Ném ``AuthError`` / ``TalkError`` / ``OSError``."""
    s = TalkSession(host, username, password, port=port)
    try:
        s.login()
    finally:
        s.close()
