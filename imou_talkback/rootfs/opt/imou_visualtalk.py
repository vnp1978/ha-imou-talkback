#!/usr/bin/env python3
"""Experimental pure-Python DHHTTP visualtalk.xav talk client."""

from __future__ import annotations
import argparse, hashlib, re, socket, struct, sys, threading, time
from pathlib import Path

try:
    from imou_dhav import build_frames
    from imou_wsse import native_nonce, password_digest, utc_created, visualtalk_password_digest
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from imou_dhav import build_frames
    from imou_wsse import native_nonce, password_digest, utc_created, visualtalk_password_digest

BASE_PATH = "/live/visualtalk.xav?channel={channel}&subtype={subtype}&encrypt={encrypt}&imagesize=18&audioType=1&trackID={track_id}&method={method}"

def default_sdp():
    return ("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=Talk\r\nc=IN IP4 0.0.0.0\r\nt=0 0\r\n"
            "m=video 0 RTP/AVP 96\r\na=control:trackID=31\r\n"
            "m=audio 0 RTP/AVP 8 96\r\na=rtpmap:8 PCMA/8000\r\n"
            "a=rtpmap:96 MPEG4-GENERIC/16000/1\r\na=control:trackID=5\r\na=sendrecv\r\n").encode()

class DHHTTPResponse:
    def __init__(self, raw_headers, body):
        self.raw_headers = raw_headers; self.body = body
        self.text = raw_headers.decode("latin1", errors="replace")
        first = self.text.split("\r\n", 1)[0]; self.status_line = first
        parts = first.split(" ", 2)
        self.code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        self.headers = {}
        for line in self.text.split("\r\n")[1:]:
            if ": " in line:
                k, v = line.split(": ", 1); self.headers[k.lower()] = v
    def private_length(self): return int(self.headers.get("private-length", "0"))
    def content_length(self): return int(self.headers.get("content-length", "0"))

class VisualTalkClient:
    def __init__(self, host, port, *, username, password, nonce, created,
                 password_digest_override, lightweight_digest, timeout):
        self.host=host; self.port=port; self.username=username; self.password=password
        self.nonce = nonce or native_nonce()
        self.created = created or utc_created()
        self.password_digest = password_digest_override or password_digest(self.nonce, self.created, password)
        self.lightweight_digest = lightweight_digest; self.timeout = timeout
        self.cseq = 0; self.digest_realm = None; self.digest_nonce = None; self.wsse_realm = None
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
    def close(self): self.sock.close()
    def wsse_value(self):
        parts = [f'Username="{self.username}"', f'PasswordDigest="{self.password_digest}"']
        if self.lightweight_digest is not None: parts.append(f'LightweightDigest="{self.lightweight_digest}"')
        parts.extend([f'Nonce="{self.nonce}"', f'Created="{self.created}"'])
        return "UsernameToken " + ", ".join(parts)
    def path(self, *, channel, subtype, encrypt, track_id, method, talktype=None):
        p = BASE_PATH.format(channel=channel, subtype=subtype, encrypt=encrypt, track_id=track_id, method=method)
        if talktype: p += f"&talktype={talktype}"
        return p
    def play(self, path, *, sdp=b"", accept_sdp=False, extra_headers=None, retry_digest=True):
        cseq = self.cseq; self.cseq += 1
        response = self._play_once(path, cseq=cseq, sdp=sdp, accept_sdp=accept_sdp, extra_headers=extra_headers)
        if retry_digest and response.code == 401 and self._parse_digest_challenge(response):
            if self.wsse_realm:
                self.password_digest = visualtalk_password_digest(self.username, self.password, self.wsse_realm, self.nonce, self.created)
            response = self._play_once(path, cseq=cseq, sdp=sdp, accept_sdp=accept_sdp, extra_headers=extra_headers)
        return response
    def _play_once(self, path, *, cseq, sdp, accept_sdp, extra_headers):
        headers = [f"PLAY {path} HTTP/1.1", f"Host: {self.host}:{self.port}",
                   "Connect-Type: P2P", "Connection: keep-alive", f"Cseq: {cseq}",
                   "Speed: 1.000000", "User-Agent: Http Stream Client/1.0"]
        if self.wsse_realm:
            headers.append('Authorization: WSSE profile="UsernameToken"')
            headers.append("WSSE: " + self.wsse_value())
        elif self.digest_realm and self.digest_nonce:
            headers.append("Authorization: " + self._digest_authorization(path))
        else:
            headers.append('Authorization: WSSE profile="UsernameToken"')
            headers.append("WSSE: " + self.wsse_value())
        if accept_sdp: headers.append("Accpet-Sdp: Private")
        if sdp:
            headers.append("Private-Type: application/sdp")
            headers.append(f"Private-Length: {len(sdp)}")
        if extra_headers: headers.extend(extra_headers)
        request = ("\r\n".join(headers) + "\r\n\r\n").encode() + sdp
        self.sock.sendall(request)
        return self.read_response()
    def _parse_digest_challenge(self, response):
        challenge = response.headers.get("www-authenticate", "")
        match = re.search(r'Digest\s+realm="([^"]+)",\s*nonce="([^"]+)"', challenge, re.I)
        if not match: return False
        self.digest_realm, self.digest_nonce = match.groups()
        self.wsse_realm = self.digest_realm
        return True
    def _digest_authorization(self, path):
        ha1 = hashlib.md5(f"{self.username}:{self.digest_realm}:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"PLAY:{path}".encode()).hexdigest()
        response = hashlib.md5(f"{ha1}:{self.digest_nonce}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{self.username}", realm="{self.digest_realm}", '
                f'nonce="{self.digest_nonce}", uri="{path}", response="{response}"')
    def read_response(self):
        header_blob = b""
        while b"\r\n\r\n" not in header_blob:
            chunk = self.sock.recv(4096)
            if not chunk: raise EOFError("socket closed before response headers")
            header_blob += chunk
            while header_blob.startswith(b"$") and len(header_blob) >= 6:
                frame_len = struct.unpack_from(">I", header_blob, 2)[0]
                total = 6 + frame_len
                while len(header_blob) < total:
                    more = self.sock.recv(total - len(header_blob))
                    if not more: raise EOFError("socket closed inside interleaved frame")
                    header_blob += more
                header_blob = header_blob[total:]
            marker = header_blob.find(b"HTTP/")
            if marker > 0: header_blob = header_blob[marker:]
        raw_headers, _, buffered = header_blob.partition(b"\r\n\r\n")
        probe = DHHTTPResponse(raw_headers, b"")
        body_len = probe.private_length() or probe.content_length()
        body = buffered
        while len(body) < body_len:
            chunk = self.sock.recv(body_len - len(body))
            if not chunk: raise EOFError("socket closed before response body")
            body += chunk
        return DHHTTPResponse(raw_headers, body[:body_len])
    def start_talk(self, args):
        sdp = Path(args.sdp).read_bytes() if args.sdp else default_sdp()
        responses = [self.play(self.path(channel=args.channel, subtype=args.subtype,
                    encrypt=args.encrypt, track_id=31, method=0), sdp=sdp, accept_sdp=True)]
        if args.open_only: return responses
        if args.track1:
            responses.append(self.play(self.path(channel=args.channel, subtype=args.subtype,
                             encrypt=args.encrypt, track_id=args.track1, method=0)))
        if args.track2:
            responses.append(self.play(self.path(channel=args.channel, subtype=args.subtype,
                             encrypt=args.encrypt, track_id=args.track2, method=0)))
        responses.append(self.play(self.path(channel=args.channel, subtype=args.subtype,
                         encrypt=args.encrypt, track_id=args.talk_track, method=0, talktype="talk")))
        return responses
    def send_audio(self, raw_audio, args):
        count = 0; frame_duration = args.frame_ms / 1000.0; next_send = time.monotonic()
        stop_drain = threading.Event()
        drain_thread = threading.Thread(target=self._drain_interleaved, args=(stop_drain,), daemon=True)
        drain_thread.start()
        try:
            for frame in build_frames(raw_audio, codec=args.codec, sample_rate=args.sample_rate,
                                      frame_ms=args.frame_ms, interleaved=True, track_id=args.media_track):
                self.sock.sendall(frame); count += 1; next_send += frame_duration
                sleep_for = next_send - time.monotonic()
                if sleep_for > 0: time.sleep(sleep_for)
        finally:
            stop_drain.set(); drain_thread.join(timeout=0.2)
        return count
    def _drain_interleaved(self, stop):
        while not stop.is_set():
            try: data = self.sock.recv(4096)
            except TimeoutError: continue
            except OSError: return
            if not data: return

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=8086)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--nonce"); parser.add_argument("--created")
    parser.add_argument("--password-digest"); parser.add_argument("--lightweight-digest")
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--subtype", type=int, default=0)
    parser.add_argument("--encrypt", type=int, default=3)
    parser.add_argument("--track1", type=int, default=6)
    parser.add_argument("--track2", type=int, default=0)
    parser.add_argument("--talk-track", type=int, default=64)
    parser.add_argument("--media-track", type=int, default=5)
    parser.add_argument("--sdp"); parser.add_argument("--audio")
    parser.add_argument("--open-only", action="store_true")
    parser.add_argument("--codec", choices=("mulaw","alaw","aac-adts","aac-raw","copy"), default="mulaw")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--frame-ms", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args()

def main():
    args = parse_args()
    client = VisualTalkClient(args.host, args.port, username=args.username, password=args.password,
                              nonce=args.nonce, created=args.created,
                              password_digest_override=args.password_digest,
                              lightweight_digest=args.lightweight_digest, timeout=args.timeout)
    try:
        responses = client.start_talk(args)
        for index, response in enumerate(responses):
            print(f"Cseq {index}: {response.status_line} body={len(response.body)}")
            if response.code >= 400:
                print(response.text, file=sys.stderr); return 1
        if args.audio:
            audio = Path(args.audio).read_bytes()
            count = client.send_audio(audio, args)
            print(f"sent {count} interleaved DHAV frames")
    finally:
        client.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
