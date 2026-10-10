"""Minimal ClamAV daemon client: the ``INSTREAM`` command over TCP.

Protocol (``man clamd``): send ``zINSTREAM\\0``, then the file as chunks each prefixed with its
length (4 bytes, network order), then a zero-length chunk; clamd answers one ``\\0``-terminated
line: ``stream: OK``, ``stream: <signature> FOUND`` or ``<reason> ERROR``.

Any connection, timeout or protocol problem (including an ``ERROR`` reply, e.g. the stream
exceeding clamd's ``StreamMaxLength``) raises ``ScannerUnavailable``: the caller retries, and
nothing is published without a scan. Settings: ``CLAMAV_HOST``, ``CLAMAV_PORT``,
``CLAMAV_TIMEOUT_SECONDS``.
"""

import socket
import struct
from dataclasses import dataclass

from django.conf import settings

CHUNK_SIZE = 64 * 1024
MAX_REPLY_BYTES = 4096


class ScannerUnavailable(Exception):
    """The scan could not be completed (daemon unreachable, timeout or error reply)."""


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str = ""


def _read_reply(sock) -> bytes:
    reply = bytearray()
    while b"\0" not in reply:
        data = sock.recv(1024)
        if not data:
            break
        reply.extend(data)
        if len(reply) > MAX_REPLY_BYTES:
            raise ScannerUnavailable("clamd reply too long")
    return bytes(reply).split(b"\0", 1)[0]


def parse_reply(raw: bytes) -> ScanResult:
    """Interpret one clamd ``INSTREAM`` reply line."""
    reply = raw.decode("utf-8", "replace").strip()
    if not reply:
        raise ScannerUnavailable("empty reply from clamd")
    if reply.endswith("ERROR"):
        raise ScannerUnavailable(f"clamd error: {reply[:200]}")
    _, _, verdict = reply.rpartition(": ")  # "stream: …" or, with session ids, "1: stream: …"
    verdict = verdict or reply
    if verdict == "OK":
        return ScanResult(clean=True)
    if verdict.endswith(" FOUND"):
        signature = verdict[: -len(" FOUND")].strip() or "unknown"
        return ScanResult(clean=False, signature=signature[:200])
    raise ScannerUnavailable(f"unexpected reply from clamd: {reply[:200]}")


def scan_stream(fileobj) -> ScanResult:
    """Scan the content of the binary file object ``fileobj`` (read to the end in chunks)."""
    address = (settings.CLAMAV_HOST, settings.CLAMAV_PORT)
    timeout = settings.CLAMAV_TIMEOUT_SECONDS
    try:
        with socket.create_connection(address, timeout=timeout) as sock:
            sock.sendall(b"zINSTREAM\0")
            while chunk := fileobj.read(CHUNK_SIZE):
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))
            raw = _read_reply(sock)
    except ScannerUnavailable:
        raise
    except (OSError, ValueError) as exc:  # refused, reset, timeout, DNS...
        raise ScannerUnavailable(f"clamd unreachable: {exc.__class__.__name__}") from exc
    return parse_reply(raw)
