"""``documents.scanner`` against a fake clamd speaking INSTREAM on a local TCP port."""

import io
import socket
import socketserver
import struct
import threading

import pytest

from documents import scanner
from documents.scanner import ScannerUnavailable, ScanResult, parse_reply, scan_stream

from .samples import EICAR


class FakeClamd(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, reply=None):
        super().__init__(("127.0.0.1", 0), FakeClamdHandler)
        self.reply = reply  # fixed reply, or None: OK / EICAR FOUND by content
        self.received: list[bytes] = []
        self.commands: list[bytes] = []


class FakeClamdHandler(socketserver.BaseRequestHandler):
    def _read_exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.request.recv(size - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    def handle(self):
        command = b""
        while not command.endswith(b"\0"):
            command += self._read_exact(1)
        self.server.commands.append(command)
        content = b""
        while True:
            (length,) = struct.unpack("!L", self._read_exact(4))
            if length == 0:
                break
            content += self._read_exact(length)
        self.server.received.append(content)
        if self.server.reply is not None:
            reply = self.server.reply
        elif EICAR in content:
            reply = b"stream: Eicar-Test-Signature FOUND"
        else:
            reply = b"stream: OK"
        self.request.sendall(reply + b"\0")


@pytest.fixture
def clamd(settings):
    servers = []

    def _start(reply=None):
        server = FakeClamd(reply)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        settings.CLAMAV_HOST, settings.CLAMAV_PORT = server.server_address
        settings.CLAMAV_TIMEOUT_SECONDS = 5
        return server

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_clean_stream(clamd, monkeypatch):
    monkeypatch.setattr(scanner, "CHUNK_SIZE", 10)
    server = clamd()
    content = b"harmless content " * 20
    assert scan_stream(io.BytesIO(content)) == ScanResult(clean=True, signature="")
    assert server.commands == [b"zINSTREAM\0"]
    assert server.received == [content]


def test_eicar_found(clamd):
    clamd()
    result = scan_stream(io.BytesIO(b"prefix " + EICAR + b" suffix"))
    assert result == ScanResult(clean=False, signature="Eicar-Test-Signature")


def test_error_reply_raises(clamd):
    clamd(reply=b"INSTREAM size limit exceeded. ERROR")
    with pytest.raises(ScannerUnavailable, match="size limit"):
        scan_stream(io.BytesIO(b"data"))


def test_unexpected_reply_raises(clamd):
    clamd(reply=b"something odd")
    with pytest.raises(ScannerUnavailable):
        scan_stream(io.BytesIO(b"data"))


def test_connection_refused(settings):
    with socket.socket() as probe:  # a port nobody listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings.CLAMAV_HOST, settings.CLAMAV_PORT = "127.0.0.1", port
    settings.CLAMAV_TIMEOUT_SECONDS = 2
    with pytest.raises(ScannerUnavailable):
        scan_stream(io.BytesIO(b"data"))


def test_timeout(settings):
    with socket.socket() as silent:  # accepts the connection, never answers
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        settings.CLAMAV_HOST, settings.CLAMAV_PORT = silent.getsockname()
        settings.CLAMAV_TIMEOUT_SECONDS = 0.3
        with pytest.raises(ScannerUnavailable):
            scan_stream(io.BytesIO(b"data"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"stream: OK", ScanResult(clean=True)),
        (b"stream: Win.Test.EICAR_HDB-1 FOUND", ScanResult(False, "Win.Test.EICAR_HDB-1")),
        (b"1: stream: OK", ScanResult(clean=True)),
    ],
)
def test_parse_reply(raw, expected):
    assert parse_reply(raw) == expected


@pytest.mark.parametrize("raw", [b"", b"stream: weird", b"Can't allocate memory ERROR"])
def test_parse_reply_errors(raw):
    with pytest.raises(ScannerUnavailable):
        parse_reply(raw)
