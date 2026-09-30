#!/usr/bin/env python3
"""Pure-Python Imou/Dahua WSSE and device-auth helpers."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import os
import random
import string
import time
from dataclasses import dataclass

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    Cipher = algorithms = modes = default_backend = None

DHP2P_REALM = "DHP2P"
DEFAULT_RAND_SALT = "5daf91fc5cfc1be8e081cfb08f792726"
DEVICE_AUTH_IV = b"2z52*lk9o6HRyJrf"
NATIVE_NONCE_ALPHABET = string.ascii_lowercase + string.ascii_uppercase + string.digits

AES_SBOX = [
    0x63,0x7C,0x77,0x7B,0xF2,0x6B,0x6F,0xC5,0x30,0x01,0x67,0x2B,0xFE,0xD7,0xAB,0x76,
    0xCA,0x82,0xC9,0x7D,0xFA,0x59,0x47,0xF0,0xAD,0xD4,0xA2,0xAF,0x9C,0xA4,0x72,0xC0,
    0xB7,0xFD,0x93,0x26,0x36,0x3F,0xF7,0xCC,0x34,0xA5,0xE5,0xF1,0x71,0xD8,0x31,0x15,
    0x04,0xC7,0x23,0xC3,0x18,0x96,0x05,0x9A,0x07,0x12,0x80,0xE2,0xEB,0x27,0xB2,0x75,
    0x09,0x83,0x2C,0x1A,0x1B,0x6E,0x5A,0xA0,0x52,0x3B,0xD6,0xB3,0x29,0xE3,0x2F,0x84,
    0x53,0xD1,0x00,0xED,0x20,0xFC,0xB1,0x5B,0x6A,0xCB,0xBE,0x39,0x4A,0x4C,0x58,0xCF,
    0xD0,0xEF,0xAA,0xFB,0x43,0x4D,0x33,0x85,0x45,0xF9,0x02,0x7F,0x50,0x3C,0x9F,0xA8,
    0x51,0xA3,0x40,0x8F,0x92,0x9D,0x38,0xF5,0xBC,0xB6,0xDA,0x21,0x10,0xFF,0xF3,0xD2,
    0xCD,0x0C,0x13,0xEC,0x5F,0x97,0x44,0x17,0xC4,0xA7,0x7E,0x3D,0x64,0x5D,0x19,0x73,
    0x60,0x81,0x4F,0xDC,0x22,0x2A,0x90,0x88,0x46,0xEE,0xB8,0x14,0xDE,0x5E,0x0B,0xDB,
    0xE0,0x32,0x3A,0x0A,0x49,0x06,0x24,0x5C,0xC2,0xD3,0xAC,0x62,0x91,0x95,0xE4,0x79,
    0xE7,0xC8,0x37,0x6D,0x8D,0xD5,0x4E,0xA9,0x6C,0x56,0xF4,0xEA,0x65,0x7A,0xAE,0x08,
    0xBA,0x78,0x25,0x2E,0x1C,0xA6,0xB4,0xC6,0xE8,0xDD,0x74,0x1F,0x4B,0xBD,0x8B,0x8A,
    0x70,0x3E,0xB5,0x66,0x48,0x03,0xF6,0x0E,0x61,0x35,0x57,0xB9,0x86,0xC1,0x1D,0x9E,
    0xE1,0xF8,0x98,0x11,0x69,0xD9,0x8E,0x94,0x9B,0x1E,0x87,0xE9,0xCE,0x55,0x28,0xDF,
    0x8C,0xA1,0x89,0x0D,0xBF,0xE6,0x42,0x68,0x41,0x99,0x2D,0x0F,0xB0,0x54,0xBB,0x16,
]
AES_RCON = [0x00,0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1B,0x36]

def utc_created(now=None):
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def native_nonce(length=32):
    raw = os.urandom(length)
    return "".join(NATIVE_NONCE_ALPHABET[b % len(NATIVE_NONCE_ALPHABET)] for b in raw)

def numeric_nonce():
    return random.randrange(2**31)

def password_digest(nonce, created, secret):
    data = f"{nonce}{created}{secret}".encode()
    return base64.b64encode(hashlib.sha1(data).digest()).decode()

def dhp2p_password_digest(username, userkey, nonce, created):
    return password_digest(nonce, created, f"{DHP2P_REALM}:{username}:{userkey}")

def wsse_header(username, digest, nonce, created):
    return (f'UsernameToken Username="{username}", PasswordDigest="{digest}", '
            f'Nonce="{nonce}", Created="{created}"')

def dhp2p_wsse_header(username, userkey, *, nonce=None, created=None):
    if nonce is None: nonce = numeric_nonce()
    if created is None: created = utc_created()
    return wsse_header(username, dhp2p_password_digest(username, userkey, nonce, created), nonce, created)

def device_login_key(username, password, rand_salt=DEFAULT_RAND_SALT):
    return hashlib.md5(f"{username}:Login to {rand_salt}:{password}".encode()).hexdigest().upper().encode()

def visualtalk_password_digest(username, password, realm, nonce, created):
    key = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest().upper()
    return password_digest(nonce, created, key)

def _xor_bytes(left, right):
    return bytes(a ^ b for a, b in zip(left, right, strict=False))

def _aes_xtime(value):
    return ((value << 1) ^ 0x1B) & 0xFF if value & 0x80 else (value << 1) & 0xFF

def _aes_mix_single_column(state, offset):
    a0,a1,a2,a3 = state[offset:offset+4]
    total = a0^a1^a2^a3
    state[offset+0] ^= total^_aes_xtime(a0^a1)
    state[offset+1] ^= total^_aes_xtime(a1^a2)
    state[offset+2] ^= total^_aes_xtime(a2^a3)
    state[offset+3] ^= total^_aes_xtime(a3^a0)

def _aes_add_round_key(state, rk):
    for i,v in enumerate(rk): state[i] ^= v

def _aes_sub_bytes(state):
    for i,v in enumerate(state): state[i] = AES_SBOX[v]

def _aes_shift_rows(state):
    old = state[:]
    state[1],state[5],state[9],state[13]  = old[5],old[9],old[13],old[1]
    state[2],state[6],state[10],state[14] = old[10],old[14],old[2],old[6]
    state[3],state[7],state[11],state[15] = old[15],old[3],old[7],old[11]

def _aes_mix_columns(state):
    for offset in range(0,16,4): _aes_mix_single_column(state,offset)

def _aes_key_expansion_256(key):
    if len(key)!=32: raise ValueError("AES-256 key must be 32 bytes")
    words = [bytearray(key[i:i+4]) for i in range(0,32,4)]
    for i in range(8,60):
        temp = words[i-1][:]
        if i%8==0:
            temp = temp[1:]+temp[:1]
            temp = bytearray(AES_SBOX[b] for b in temp)
            temp[0] ^= AES_RCON[i//8]
        elif i%8==4:
            temp = bytearray(AES_SBOX[b] for b in temp)
        words.append(bytearray(a^b for a,b in zip(words[i-8],temp,strict=True)))
    return [bytes().join(words[i:i+4]) for i in range(0,60,4)]

def aes256_encrypt_block(key, block):
    rks = _aes_key_expansion_256(key)
    state = bytearray(block)
    _aes_add_round_key(state,rks[0])
    for r in range(1,14):
        _aes_sub_bytes(state); _aes_shift_rows(state); _aes_mix_columns(state); _aes_add_round_key(state,rks[r])
    _aes_sub_bytes(state); _aes_shift_rows(state); _aes_add_round_key(state,rks[14])
    return bytes(state)

def aes256_ofb_crypt(key, iv, data):
    out = bytearray(); stream = iv
    for offset in range(0,len(data),16):
        stream = aes256_encrypt_block(key,stream)
        chunk = data[offset:offset+16]
        out.extend(_xor_bytes(chunk,stream[:len(chunk)]))
    return bytes(out)

def device_auth_aes_key(key, nonce):
    return hashlib.pbkdf2_hmac("sha256", key, str(nonce).encode(), 20000, 32)

def encrypt_local_addr(key, nonce, local_addr_xml):
    aes_key = device_auth_aes_key(key, nonce)
    if Cipher is None:
        encrypted = aes256_ofb_crypt(aes_key, DEVICE_AUTH_IV, local_addr_xml.encode())
    else:
        enc = Cipher(algorithms.AES(aes_key), modes.OFB(DEVICE_AUTH_IV), backend=default_backend()).encryptor()
        encrypted = enc.update(local_addr_xml.encode()) + enc.finalize()
    return base64.b64encode(encrypted).decode()

def decrypt_local_addr(key, nonce, encrypted_local_addr):
    aes_key = device_auth_aes_key(key, nonce)
    encrypted = base64.b64decode(encrypted_local_addr)
    if Cipher is None:
        decrypted = aes256_ofb_crypt(aes_key, DEVICE_AUTH_IV, encrypted)
    else:
        dec = Cipher(algorithms.AES(aes_key), modes.OFB(DEVICE_AUTH_IV), backend=default_backend()).decryptor()
        decrypted = dec.update(encrypted) + dec.finalize()
    return decrypted.decode()

def device_auth_digest(key, nonce, created_epoch, payload=""):
    msg = f"{nonce}{created_epoch}{payload}".encode()
    return base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()

@dataclass(frozen=True)
class DeviceAuthFields:
    create_date: int; dev_auth: str; nonce: object; rand_salt: str; username: str
    def to_xml_fields(self):
        return (f"<CreateDate>{self.create_date}</CreateDate><DevAuth>{self.dev_auth}</DevAuth>"
                f"<Nonce>{self.nonce}</Nonce><RandSalt>{self.rand_salt}</RandSalt><UserName>{self.username}</UserName>")

def build_device_auth_fields(username, password, *, nonce=None, rand_salt=DEFAULT_RAND_SALT, created_epoch=None, payload=""):
    if nonce is None: nonce = numeric_nonce()
    if created_epoch is None: created_epoch = int(time.time())
    key = device_login_key(username, password, rand_salt)
    return DeviceAuthFields(create_date=created_epoch, dev_auth=device_auth_digest(key,nonce,created_epoch,payload),
                            nonce=nonce, rand_salt=rand_salt, username=username)

if __name__ == "__main__":
    pass
