from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct


MAGIC = b"ALR1"
S_BOX = [
    99, 124, 119, 123, 242, 107, 111, 197, 48, 1, 103, 43, 254, 215, 171, 118,
    202, 130, 201, 125, 250, 89, 71, 240, 173, 212, 162, 175, 156, 164, 114, 192,
    183, 253, 147, 38, 54, 63, 247, 204, 52, 165, 229, 241, 113, 216, 49, 21,
    4, 199, 35, 195, 24, 150, 5, 154, 7, 18, 128, 226, 235, 39, 178, 117,
    9, 131, 44, 26, 27, 110, 90, 160, 82, 59, 214, 179, 41, 227, 47, 132,
    83, 209, 0, 237, 32, 252, 177, 91, 106, 203, 190, 57, 74, 76, 88, 207,
    208, 239, 170, 251, 67, 77, 51, 133, 69, 249, 2, 127, 80, 60, 159, 168,
    81, 163, 64, 143, 146, 157, 56, 245, 188, 182, 218, 33, 16, 255, 243, 210,
    205, 12, 19, 236, 95, 151, 68, 23, 196, 167, 126, 61, 100, 93, 25, 115,
    96, 129, 79, 220, 34, 42, 144, 136, 70, 238, 184, 20, 222, 94, 11, 219,
    224, 50, 58, 10, 73, 6, 36, 92, 194, 211, 172, 98, 145, 149, 228, 121,
    231, 200, 55, 109, 141, 213, 78, 169, 108, 86, 244, 234, 101, 122, 174, 8,
    186, 120, 37, 46, 28, 166, 180, 198, 232, 221, 116, 31, 75, 189, 139, 138,
    112, 62, 181, 102, 72, 3, 246, 14, 97, 53, 87, 185, 134, 193, 29, 158,
    225, 248, 152, 17, 105, 217, 142, 148, 155, 30, 135, 233, 206, 85, 40, 223,
    140, 161, 137, 13, 191, 230, 66, 104, 65, 153, 45, 15, 176, 84, 187, 22,
]
RCON = [0, 1, 2, 4, 8, 16, 32, 64, 128, 27, 54]


def encrypt_payload(payload: dict, public_modulus: str, public_exponent: str) -> str:
    plain = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    aes_key = os.urandom(32)
    hmac_key = os.urandom(32)
    iv = os.urandom(16)
    cipher_bytes = aes_cbc_encrypt(aes_key, iv, pkcs7_pad(plain))
    encrypted_key = rsa_encrypt_pkcs1_v15(aes_key + hmac_key, public_modulus, public_exponent)
    unsigned = MAGIC + _chunk(encrypted_key) + _chunk(iv) + _chunk(cipher_bytes)
    signature = hmac.new(hmac_key, unsigned, hashlib.sha256).digest()
    return base64.b64encode(unsigned + _chunk(signature)).decode("ascii")


def rsa_encrypt_pkcs1_v15(message: bytes, modulus_b64: str, exponent_b64: str) -> bytes:
    n = int.from_bytes(base64.b64decode(modulus_b64), "big")
    e = int.from_bytes(base64.b64decode(exponent_b64), "big")
    key_size = (n.bit_length() + 7) // 8

    if len(message) > key_size - 11:
        raise ValueError("Message too long for RSA key")

    ps_len = key_size - len(message) - 3
    ps = bytearray()

    while len(ps) < ps_len:
        chunk = os.urandom(ps_len - len(ps))
        ps.extend(byte for byte in chunk if byte != 0)

    encoded = b"\x00\x02" + bytes(ps[:ps_len]) + b"\x00" + message
    encrypted = pow(int.from_bytes(encoded, "big"), e, n)
    return encrypted.to_bytes(key_size, "big")


def pkcs7_pad(data: bytes) -> bytes:
    pad_len = 16 - (len(data) % 16)
    return data + bytes([pad_len]) * pad_len


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    expanded = expand_key(key)
    previous = iv
    output = bytearray()

    for offset in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[offset : offset + 16], previous))
        encrypted = encrypt_block(block, expanded)
        output.extend(encrypted)
        previous = encrypted

    return bytes(output)


def encrypt_block(block: bytes, expanded_key: list[int]) -> bytes:
    state = list(block)
    add_round_key(state, expanded_key, 0)

    for round_index in range(1, 14):
        sub_bytes(state)
        shift_rows(state)
        mix_columns(state)
        add_round_key(state, expanded_key, round_index)

    sub_bytes(state)
    shift_rows(state)
    add_round_key(state, expanded_key, 14)
    return bytes(state)


def expand_key(key: bytes) -> list[int]:
    if len(key) != 32:
        raise ValueError("AES-256 key must be 32 bytes")

    words = [list(key[index : index + 4]) for index in range(0, 32, 4)]

    for index in range(8, 60):
        temp = words[index - 1].copy()

        if index % 8 == 0:
            temp = temp[1:] + temp[:1]
            temp = [S_BOX[value] for value in temp]
            temp[0] ^= RCON[index // 8]
        elif index % 8 == 4:
            temp = [S_BOX[value] for value in temp]

        words.append([a ^ b for a, b in zip(words[index - 8], temp)])

    expanded = []

    for word in words:
        expanded.extend(word)

    return expanded


def add_round_key(state: list[int], expanded_key: list[int], round_index: int) -> None:
    offset = round_index * 16

    for index in range(16):
        state[index] ^= expanded_key[offset + index]


def sub_bytes(state: list[int]) -> None:
    for index, value in enumerate(state):
        state[index] = S_BOX[value]


def shift_rows(state: list[int]) -> None:
    state[1], state[5], state[9], state[13] = state[5], state[9], state[13], state[1]
    state[2], state[6], state[10], state[14] = state[10], state[14], state[2], state[6]
    state[3], state[7], state[11], state[15] = state[15], state[3], state[7], state[11]


def mix_columns(state: list[int]) -> None:
    for column in range(4):
        offset = column * 4
        a0, a1, a2, a3 = state[offset : offset + 4]
        state[offset] = mul2(a0) ^ mul3(a1) ^ a2 ^ a3
        state[offset + 1] = a0 ^ mul2(a1) ^ mul3(a2) ^ a3
        state[offset + 2] = a0 ^ a1 ^ mul2(a2) ^ mul3(a3)
        state[offset + 3] = mul3(a0) ^ a1 ^ a2 ^ mul2(a3)


def mul2(value: int) -> int:
    result = value << 1

    if value & 0x80:
        result ^= 0x1B

    return result & 0xFF


def mul3(value: int) -> int:
    return mul2(value) ^ value


def _chunk(data: bytes) -> bytes:
    return struct.pack("<I", len(data)) + data
