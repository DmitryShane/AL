from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"ALR1"


@dataclass(frozen=True)
class DecodedReport:
    payload: dict


@dataclass(frozen=True)
class ReportChallengeKeys:
    private_key_pem: str
    public_modulus: str
    public_exponent: str


def generate_report_challenge_keys() -> ReportChallengeKeys:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_numbers = private_key.public_key().public_numbers()
    modulus = _int_to_base64(public_numbers.n)
    exponent = _int_to_base64(public_numbers.e)
    return ReportChallengeKeys(
        private_key_pem=private_key_pem,
        public_modulus=modulus,
        public_exponent=exponent,
    )


def _read_int(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ValueError("Unexpected end of packet while reading length")

    return struct.unpack_from("<I", data, offset)[0], offset + 4


def _read_bytes(data: bytes, offset: int) -> tuple[bytes, int]:
    length, offset = _read_int(data, offset)
    end = offset + length

    if end > len(data):
        raise ValueError("Unexpected end of packet while reading bytes")

    return data[offset:end], end


def decode_alr1(private_key_pem: str, encrypted_packet: str) -> DecodedReport:
    packet = base64.b64decode(encrypted_packet)

    if packet[:4] != MAGIC:
        raise ValueError("Unsupported AL report packet")

    offset = 4
    encrypted_key, offset = _read_bytes(packet, offset)
    iv, offset = _read_bytes(packet, offset)
    cipher_bytes, offset = _read_bytes(packet, offset)
    unsigned_packet = packet[:offset]
    signature, offset = _read_bytes(packet, offset)

    if offset != len(packet):
        raise ValueError("Unexpected trailing packet data")

    private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    key_material = private_key.decrypt(encrypted_key, padding.PKCS1v15())

    if len(key_material) != 64:
        raise ValueError("Unexpected decrypted key length")

    aes_key = key_material[:32]
    hmac_key = key_material[32:]
    expected_signature = hmac.new(hmac_key, unsigned_packet, hashlib.sha256).digest()

    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("HMAC verification failed")

    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(cipher_bytes) + decryptor.finalize()
    pad_length = padded[-1]

    if pad_length < 1 or pad_length > 16:
        raise ValueError("Invalid AES padding")

    if padded[-pad_length:] != bytes([pad_length]) * pad_length:
        raise ValueError("Invalid AES padding bytes")

    payload = json.loads(padded[:-pad_length].decode("utf-8"))
    return DecodedReport(payload=payload)


def _int_to_base64(value: int) -> str:
    byte_length = max(1, (value.bit_length() + 7) // 8)
    return base64.b64encode(value.to_bytes(byte_length, "big")).decode("ascii")
