#!/usr/bin/env python3
import base64
import datetime as dt
import hashlib
import hmac
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
KEY_PATH = SCRIPT_DIR / "UnityActivityLoggerKey.json"
DEFAULT_LOG_DIR = Path("/Volumes/MacMiniExternal2TB/Development/unity-bike-rush-2/Assets/Plugins/UAL")


def read_int(data, offset):
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def read_bytes(data, offset):
    length, offset = read_int(data, offset)
    return data[offset:offset + length], offset + length


def decrypt_rsa(private_pem, encrypted_key):
    with tempfile.TemporaryDirectory() as temp_dir:
        private_path = Path(temp_dir) / "private.pem"
        encrypted_path = Path(temp_dir) / "key.bin"
        decrypted_path = Path(temp_dir) / "key.out"

        private_path.write_text(private_pem)
        encrypted_path.write_bytes(encrypted_key)

        subprocess.check_call(
            [
                "openssl",
                "pkeyutl",
                "-decrypt",
                "-inkey",
                str(private_path),
                "-in",
                str(encrypted_path),
                "-out",
                str(decrypted_path),
                "-pkeyopt",
                "rsa_padding_mode:pkcs1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return decrypted_path.read_bytes()


def decrypt_aes(aes_key, iv, cipher_bytes):
    with tempfile.TemporaryDirectory() as temp_dir:
        cipher_path = Path(temp_dir) / "payload.bin"
        plain_path = Path(temp_dir) / "payload.out"
        cipher_path.write_bytes(cipher_bytes)

        subprocess.check_call(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-K",
                aes_key.hex(),
                "-iv",
                iv.hex(),
                "-nosalt",
                "-nopad",
                "-in",
                str(cipher_path),
                "-out",
                str(plain_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        padded = plain_path.read_bytes()
        pad_length = padded[-1]

        if pad_length < 1 or pad_length > 16:
            raise ValueError("Invalid AES padding")

        if padded[-pad_length:] != bytes([pad_length]) * pad_length:
            raise ValueError("Invalid AES padding bytes")

        return padded[:-pad_length]


def decrypt_line(private_pem, line):
    packet = base64.b64decode(line)

    if packet[:4] != b"UAL1":
        raise ValueError("Unsupported UAL packet")

    offset = 4
    encrypted_key, offset = read_bytes(packet, offset)
    iv, offset = read_bytes(packet, offset)
    cipher_bytes, offset = read_bytes(packet, offset)
    unsigned_packet = packet[:offset]
    signature, offset = read_bytes(packet, offset)

    if offset != len(packet):
        raise ValueError("Unexpected trailing data")

    key_material = decrypt_rsa(private_pem, encrypted_key)

    if len(key_material) != 64:
        raise ValueError("Unexpected decrypted key length")

    aes_key = key_material[:32]
    hmac_key = key_material[32:]
    expected_signature = hmac.new(hmac_key, unsigned_packet, hashlib.sha256).digest()

    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("HMAC verification failed")

    plain_bytes = decrypt_aes(aes_key, iv, cipher_bytes)
    return json.loads(plain_bytes.decode("utf-8"))


def parse_datetime(date_value, time_value):
    return dt.datetime.strptime(date_value + " " + time_value, "%Y-%m-%d %H:%M:%S")


def parse_recorded_at(value):
    normalized_value = value

    if normalized_value.endswith("Z"):
        normalized_value = normalized_value[:-1] + "+00:00"

    if "." in normalized_value:
        prefix, suffix = normalized_value.split(".", 1)
        timezone_index = max(suffix.find("+"), suffix.find("-"))

        if timezone_index >= 0:
            fraction = suffix[:timezone_index]
            timezone = suffix[timezone_index:]
        else:
            fraction = suffix
            timezone = ""

        normalized_value = prefix + "." + fraction[:6] + timezone

    return dt.datetime.fromisoformat(normalized_value).replace(tzinfo=None)


def format_duration(seconds):
    seconds = int(round(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes:02d}m"


def format_date(date_value):
    return dt.datetime.strptime(date_value, "%Y-%m-%d").strftime("%d-%m-%Y")


def resolve_log_path(value):
    raw_value = value.strip()

    if raw_value.startswith("/") or raw_value.startswith("~"):
        return Path(raw_value).expanduser().resolve()

    if "/" in raw_value:
        return Path(raw_value).expanduser().resolve()

    if raw_value.lower().endswith(".json"):
        return (DEFAULT_LOG_DIR / raw_value).resolve()

    lower_value = raw_value.lower()

    if lower_value.startswith("ual"):
        suffix = lower_value[3:]
    else:
        suffix = lower_value

    return (DEFAULT_LOG_DIR / f"UAL{suffix}.json").resolve()


def load_records(log_path):
    key_data = json.loads(KEY_PATH.read_text())
    private_pem = key_data["privateKeyPem"]
    records = []

    for line_number, line in enumerate(log_path.read_text().splitlines(), start=1):
        line = line.strip()

        if not line:
            continue

        try:
            records.append(decrypt_line(private_pem, line))
        except Exception as exception:
            print(f"Skipped line {line_number}: {exception}", file=sys.stderr)

    return records


def summarize(records):
    latest_by_session = {}

    for record in records:
        key = (record["date"], record["sessionId"])
        recorded_at = record.get("recordedAt", "")
        previous = latest_by_session.get(key)

        if previous is None or recorded_at > previous.get("recordedAt", ""):
            latest_by_session[key] = record

    sessions_by_date = {}

    for record in latest_by_session.values():
        sessions_by_date.setdefault(record["date"], []).append(record)

    for date_value in sorted(sessions_by_date.keys()):
        sessions = sessions_by_date[date_value]
        sessions.sort(key=lambda item: item["firstActivity"])

        first_activity = None
        last_activity = None
        active_seconds = 0
        idle_seconds = 0
        overtime_active_seconds = 0
        author = sessions[0].get("author", "Unknown User")
        latest_recorded_at = None
        work_window_seconds = 32400

        for session in sessions:
            session_first = parse_datetime(date_value, session["firstActivity"])
            session_last = parse_datetime(date_value, session["lastActivity"])
            session_active_seconds = int(session.get("activeSeconds", 0))
            session_idle_seconds = int(session.get("idleSeconds", 0))
            session_overtime_active_seconds = int(session.get("overtimeActiveSeconds", 0))
            work_window_seconds = int(session.get("workWindowSeconds", work_window_seconds))

            if session_active_seconds == 0 and session_idle_seconds > 0 and session_first == session_last:
                continue

            if first_activity is None or session_first < first_activity:
                first_activity = session_first

            if last_activity is None or session_last > last_activity:
                last_activity = session_last

            active_seconds += session_active_seconds
            idle_seconds += session_idle_seconds
            overtime_active_seconds += session_overtime_active_seconds

            recorded_at = parse_recorded_at(session.get("recordedAt", ""))

            if latest_recorded_at is None or recorded_at > latest_recorded_at:
                latest_recorded_at = recorded_at

        if first_activity is None or last_activity is None:
            continue

        if latest_recorded_at is not None:
            work_window_end = first_activity + dt.timedelta(seconds=work_window_seconds)
            idle_cap_end = latest_recorded_at

            if idle_cap_end > work_window_end:
                idle_cap_end = work_window_end

            max_idle_seconds = max(0, int((idle_cap_end - first_activity).total_seconds()) - active_seconds)

            if idle_seconds > max_idle_seconds:
                idle_seconds = max_idle_seconds

        print(f"author: {author}")
        print(f"date: {format_date(date_value)}")
        print(f"first activity: {first_activity.strftime('%H:%M:%S')}")
        print(f"last activity: {last_activity.strftime('%H:%M:%S')}")
        print(f"active duration: {format_duration(active_seconds)}")
        print(f"idle duration: {format_duration(idle_seconds)}")
        print(f"overtime active: {format_duration(overtime_active_seconds)}")
        print()


def main():
    if len(sys.argv) != 2:
        print("Usage: ual_decode.py /path/to/UALxx.json", file=sys.stderr)
        print("   or: ual_decode.py ualds", file=sys.stderr)
        return 2

    log_path = resolve_log_path(sys.argv[1])

    if not KEY_PATH.exists():
        print(f"Key file not found: {KEY_PATH}", file=sys.stderr)
        return 2

    if not log_path.exists():
        print(f"Log file not found: {log_path}", file=sys.stderr)
        return 2

    records = load_records(log_path)

    if not records:
        print("No readable UAL records found.")
        return 1

    summarize(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
