"""Nói ra loa camera Imou qua cổng 8086 — kênh nói HTTP riêng của Imou, tiếng AAC 16 kHz.

Python thuần, không phụ thuộc Home Assistant — cùng giao diện với ``talk.TalkSession``
(``with``, ``send_pcm``, ``close``, ``tan_so``).

Vì sao: chính camera khai kênh nói là ``MPEG4-GENERIC/16000`` (SDP ``trackID=5 sendonly``),
còn cổng 37777 chỉ nhận PCM 8 kHz — cắt mất dải trên 4 kHz, đúng dải phụ âm s/x/ch. Nghe so
sánh trên camera Imou thật (28/09/2026): 16 kHz rõ hơn hẳn. Bắt tay 8086 mất ~0,03 giây.

Trình tự byte theo ha-imou-talkback (vnp1978, MIT), viết lại: yêu cầu ``PLAY
/live/visualtalk.xav…`` kiểu HTTP, xác thực WSSE (UsernameToken), rồi tiếng đi trên CHÍNH
kết nối ấy dạng xen kẽ ``$<kênh><dài>`` bọc khung DHAV. ffmpeg (của HA) mã hoá AAC.

``MoPhienImou`` thử 8086 trước; camera không mở / từ chối thì lùi về 37777 và nhớ một giờ.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import struct
import subprocess
import threading
import time

from .talk import TalkError, TalkSession

CONG_HTTP = 8086
TAN_SO_HTTP = 16000
#: Một khung AAC = 1024 mẫu = 64 ms ở 16 kHz.
_KHUNG_GIAY = 1024 / TAN_SO_HTTP
#: ``send_pcm`` chỉ được đi trước thời gian thực ngần này giây: người gọi dựa vào lúc
#: ``send_pcm`` trả về để biết tiếng sắp dứt (mốc chặn mic sau tiếng ting).
_DI_TRUOC = 0.15
#: Sau khung cuối chờ ngần này giây cho camera phát nốt phần đang đệm rồi mới đóng.
_DUOI_GIAY = 0.4
_TOI_DA_GIAY = 300.0
_KENH_NOI = 5                         # trackID kênh nói trong SDP camera trả về
_DUONG = ("/live/visualtalk.xav?channel=1&subtype=0&encrypt=3&imagesize=18&audioType=1"
          "&trackID={track}&method=0")
_SDP = ("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=Talk\r\nc=IN IP4 0.0.0.0\r\nt=0 0\r\n"
        "m=video 0 RTP/AVP 96\r\na=control:trackID=31\r\n"
        "m=audio 0 RTP/AVP 8 96\r\na=rtpmap:8 PCMA/8000\r\n"
        "a=rtpmap:96 MPEG4-GENERIC/16000/1\r\na=control:trackID=5\r\na=sendrecv\r\n").encode()
_CHU_NONCE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
#: Camera từ chối 8086 thì ngần này giây sau mới thử lại.
LUI_37777_GIAY = 3600.0


def _wsse(user: str, bi_mat: str, nonce: str, tao: str) -> str:
    # SHA-1 do giao thức camera quy định (WSSE UsernameToken), không phải lựa chọn của ta.
    so = base64.b64encode(hashlib.sha1(f"{nonce}{tao}{bi_mat}".encode(),
                                       usedforsecurity=False).digest()).decode()
    return (f'UsernameToken Username="{user}", PasswordDigest="{so}", '
            f'Nonce="{nonce}", Created="{tao}"')


def khung_dhav(aac: bytes, seq: int, tick_ms: int, giay: int) -> bytes:
    """Một khung AAC → khung DHAV (tiếng, 16 kHz) bọc xen kẽ trên kênh nói."""
    dai = len(aac) + 36
    dau = bytearray(28)
    struct.pack_into("<4sB3xII", dau, 0, b"DHAV", 0xF0, seq & 0xFFFFFFFF, dai)
    struct.pack_into("<IH", dau, 0x10, giay & 0xFFFFFFFF, tick_ms & 0xFFFF)
    dau[0x16] = 0x04
    dau[0x17] = sum(dau[:0x17]) & 0xFF
    dau[0x18:0x1C] = b"\x83\x01\x1a\x04"          # tiếng; mã tần số 4 = 16 kHz
    khung = bytes(dau) + aac + b"dhav" + struct.pack("<I", dai)
    return b"$" + bytes([_KENH_NOI * 2]) + struct.pack(">I", len(khung)) + khung


def cat_adts(du: bytes) -> tuple[list[bytes], bytes]:
    """Tách các khung ADTS trọn vẹn khỏi đầu ``du``; trả (khung, phần còn dở)."""
    ra = []
    while len(du) >= 7:
        if du[0] != 0xFF or du[1] & 0xF0 != 0xF0:
            i = du.find(b"\xff", 1)
            du = du[i:] if i > 0 else b""
            continue
        dai = ((du[3] & 0x03) << 11) | (du[4] << 3) | (du[5] >> 5)
        if dai < 7 or len(du) < dai:
            break
        ra.append(du[:dai])
        du = du[dai:]
    return ra, du


class HttpTalkSession:
    """Một phiên nói qua cổng 8086. ``send_pcm`` nhận PCM16 mono 16 kHz."""

    tan_so = TAN_SO_HTTP

    def __init__(self, host: str, username: str, password: str, *, ffmpeg: str = "ffmpeg",
                 port: int = CONG_HTTP, timeout: float = 5.0) -> None:
        self.host, self.username, self.password = host, username, password
        self.ffmpeg, self.port, self.timeout = ffmpeg, port, timeout
        self.s: socket.socket | None = None
        self._ff: subprocess.Popen | None = None
        self._cseq = 0
        self._realm = ""
        self._dung = threading.Event()
        self._luong: list[threading.Thread] = []
        self._t_dau: float | None = None
        self._da_ghi = 0.0

    def __enter__(self) -> HttpTalkSession:
        try:
            self._mo()
        except BaseException:
            self.close(cho=False)
            raise
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # bắt tay -----------------------------------------------------------------
    def _play(self, track: int, *, sdp: bytes = b"", them: str = "") -> int:
        nonce = "".join(_CHU_NONCE[b % len(_CHU_NONCE)] for b in os.urandom(32))
        tao = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        bi_mat = self.password
        if self._realm:
            # Camera đòi "Digest realm": bí mật là MD5 hoa của user:realm:mật khẩu.
            bi_mat = hashlib.md5(f"{self.username}:{self._realm}:{self.password}".encode(),
                                 usedforsecurity=False).hexdigest().upper()
        dong = [f"PLAY {_DUONG.format(track=track)}{them} HTTP/1.1",
                f"Host: {self.host}:{self.port}", "Connect-Type: P2P", "Connection: keep-alive",
                f"Cseq: {self._cseq}", "Speed: 1.000000", "User-Agent: Http Stream Client/1.0",
                'Authorization: WSSE profile="UsernameToken"',
                "WSSE: " + _wsse(self.username, bi_mat, nonce, tao)]
        if sdp:
            dong += ["Accpet-Sdp: Private", "Private-Type: application/sdp",
                     f"Private-Length: {len(sdp)}"]
        self._cseq += 1
        self.s.sendall(("\r\n".join(dong) + "\r\n\r\n").encode() + sdp)
        return self._doc_tra_loi()

    def _doc_tra_loi(self) -> int:
        du = b""
        while b"\r\n\r\n" not in du:
            b = self.s.recv(4096)
            if not b:
                raise TalkError("camera closed port 8086 connection")
            du += b
            while du.startswith(b"$") and len(du) >= 6:      # khung media xen kẽ: bỏ
                dai = 6 + struct.unpack_from(">I", du, 2)[0]
                while len(du) < dai:
                    b = self.s.recv(dai - len(du))
                    if not b:
                        raise TalkError("camera closed port 8086 connection")
                    du += b
                du = du[dai:]
        dau, _, con = du.partition(b"\r\n\r\n")
        chu = dau.decode("latin1", "replace")
        m = re.match(r"\S+ (\d+)", chu)
        ma = int(m.group(1)) if m else 0
        tt = {k.lower(): v for k, _, v in (x.partition(": ") for x in chu.split("\r\n")[1:])}
        dai = int(tt.get("private-length") or tt.get("content-length") or 0)
        while len(con) < dai:
            b = self.s.recv(dai - len(con))
            if not b:
                break
            con += b
        if ma == 401 and not self._realm:
            m = re.search(r'realm="([^"]+)"', tt.get("www-authenticate", ""), re.IGNORECASE)
            self._realm = m.group(1) if m else ""
        return ma

    def _mo(self) -> None:
        self.s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        ma = self._play(31, sdp=_SDP)
        if ma == 401 and self._realm:
            ma = self._play(31, sdp=_SDP)             # một lần theo realm — không thử mãi
        for track, them in ((None, ""), (6, ""), (64, "&talktype=talk")):
            if track is not None:
                ma = self._play(track, them=them)
            if ma != 200:
                raise TalkError(f"camera refused talk on port 8086 (code {ma})")
        self._ff = subprocess.Popen(
            [self.ffmpeg, "-hide_banner", "-loglevel", "error",
             "-probesize", "32", "-analyzeduration", "0", "-fflags", "nobuffer",
             "-f", "s16le", "-ar", str(TAN_SO_HTTP), "-ac", "1", "-i", "pipe:0",
             "-c:a", "aac", "-b:a", "48k", "-f", "adts", "-flush_packets", "1", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.s.settimeout(1.0)
        self._luong = [threading.Thread(target=self._xa, name="dahua-talk-8086-doc", daemon=True),
                       threading.Thread(target=self._gui, args=(self._ff,),
                                        name="dahua-talk-8086-gui", daemon=True)]
        for t in self._luong:
            t.start()

    # tiếng -------------------------------------------------------------------
    def _xa(self) -> None:
        """Đọc bỏ tiếng/hình camera gửi về trên cùng kết nối, cho bộ đệm khỏi đầy."""
        while not self._dung.is_set():
            try:
                if not self.s.recv(65536):
                    return
            except TimeoutError:
                continue
            except OSError:
                return

    def _gui(self, ff: subprocess.Popen) -> None:
        # Giữ ``ff`` riêng: ``close`` gỡ ``self._ff`` ngay khi hết tiếng, luồng này còn gửi nốt.
        du, seq, t0 = b"", 0, None
        tick, giay = int(time.monotonic() * 1000), int(time.time())
        try:
            while True:
                b = ff.stdout.read1(4096)
                if not b:
                    break
                khung, du = cat_adts(du + b)
                for k in khung:
                    if t0 is None:
                        t0 = time.monotonic()
                    cho = t0 + seq * _KHUNG_GIAY - time.monotonic()
                    if cho > 0:
                        time.sleep(cho)
                    self.s.sendall(khung_dhav(k, seq, tick + int(seq * 64), giay))
                    seq += 1
        except (OSError, ValueError, AttributeError):
            pass

    def send_pcm(self, pcm: bytes) -> None:
        """PCM16 LE mono 16 kHz. Trả về khi tiếng ấy sắp phát xong (đi trước thời gian thực
        tối đa ``_DI_TRUOC`` giây) — như ``TalkSession.send_pcm``."""
        if self._t_dau is None:
            self._t_dau = time.monotonic()
        try:
            self._ff.stdin.write(pcm)
            self._ff.stdin.flush()
        except (BrokenPipeError, ValueError, AttributeError) as exc:
            raise TalkError(f"audio encoder stopped ({exc})") from exc
        self._da_ghi += len(pcm) / (2 * TAN_SO_HTTP)
        cho = self._t_dau + self._da_ghi - _DI_TRUOC - time.monotonic()
        if cho > 0:
            time.sleep(cho)

    def close(self, cho: bool = True) -> None:
        ff, self._ff = self._ff, None
        if ff is not None:
            try:
                ff.stdin.close()
            except OSError:
                pass
            if cho:
                for t in self._luong:
                    if t.name == "dahua-talk-8086-gui":
                        t.join(_TOI_DA_GIAY)
                time.sleep(_DUOI_GIAY)
            if ff.poll() is None:
                ff.kill()
            ff.wait()
        self._dung.set()
        if self.s is not None:
            try:
                self.s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.s.close()
            self.s = None
        for t in self._luong:
            if t is not threading.current_thread():
                t.join(2)


class _PhienImou:
    """Một lần nói: ``with`` mở 8086 (hoặc 37777 dự phòng) và trả phiên đã mở."""

    def __init__(self, mo: MoPhienImou) -> None:
        self._mo, self._s = mo, None

    def __enter__(self):
        self._s = self._mo.mo_that()
        return self._s

    def __exit__(self, *_exc) -> None:
        if self._s is not None:
            self._s.close()


class MoPhienImou:
    """Hàm mở phiên nói cho camera Imou/Dahua: 8086 (16 kHz) trước, 37777 (8 kHz) dự phòng.

    ``tan_so`` = tần số phiên sắp mở — ``Speaker`` sinh tiếng đúng tần số ấy."""

    def __init__(self, host: str, username: str, password: str, *, port: int = 37777,
                 ffmpeg: str = "ffmpeg") -> None:
        self.host, self.username, self.password = host, username, password
        self.port, self.ffmpeg = port, ffmpeg
        self.lui_toi = 0.0                 # monotonic: trước mốc này đi thẳng 37777

    @property
    def tan_so(self) -> int:
        return TAN_SO_HTTP if self.lui_toi <= time.monotonic() else TalkSession.tan_so

    def __call__(self) -> _PhienImou:
        return _PhienImou(self)

    def mo_that(self):
        if self.lui_toi <= time.monotonic():
            try:
                return HttpTalkSession(self.host, self.username, self.password,
                                       ffmpeg=self.ffmpeg).__enter__()
            except (TalkError, OSError):
                self.lui_toi = time.monotonic() + LUI_37777_GIAY
        return TalkSession(self.host, self.username, self.password, port=self.port).__enter__()


__all__ = ["CONG_HTTP", "HttpTalkSession", "MoPhienImou", "TAN_SO_HTTP", "cat_adts", "khung_dhav"]
