import base64
import hashlib
import hmac
import json
import struct
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from al_backend.protocol import MAGIC, decode_alr1


PRIVATE_KEY = json.loads(
    (Path(__file__).resolve().parents[1] / "al_backend" / "UnityActivityLoggerKey.json").read_text()
)["privateKeyPem"]


def test_decode_alr1_round_trip():
    payload = {"source": "ual", "author": "Dmitry Shane", "activeSeconds": 12}
    packet = _encode(payload)

    decoded = decode_alr1(PRIVATE_KEY, packet)

    assert decoded.payload == payload


def test_decode_rejects_wrong_magic():
    packet = base64.b64encode(b"UAL1").decode("ascii")

    try:
        decode_alr1(PRIVATE_KEY, packet)
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("decode_alr1 should reject non-ALR1 packets")


def _encode(payload: dict) -> str:
    private_key_candidate = serialization.load_pem_private_key(PRIVATE_KEY.encode("utf-8"), password=None)

    if not isinstance(private_key_candidate, rsa.RSAPrivateKey):
        raise AssertionError("UnityActivityLoggerKey must be RSA for ALR1 test encoding")

    public_key_candidate = private_key_candidate.public_key()

    if not isinstance(public_key_candidate, rsa.RSAPublicKey):
        raise AssertionError("UnityActivityLoggerKey must yield RSA public key for ALR1 test encoding")

    aes_key = bytes(range(32))
    hmac_key = bytes(range(32, 64))
    iv = bytes(range(16))
    plain = json.dumps(payload).encode("utf-8")
    pad_len = 16 - (len(plain) % 16)
    padded = plain + bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    cipher_bytes = encryptor.update(padded) + encryptor.finalize()
    encrypted_key = public_key_candidate.encrypt(aes_key + hmac_key, padding.PKCS1v15())
    unsigned_packet = MAGIC + _bytes(encrypted_key) + _bytes(iv) + _bytes(cipher_bytes)
    signature = hmac.new(hmac_key, unsigned_packet, hashlib.sha256).digest()
    return base64.b64encode(unsigned_packet + _bytes(signature)).decode("ascii")


def _bytes(value: bytes) -> bytes:
    return struct.pack("<I", len(value)) + value
