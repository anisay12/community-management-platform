"""Minimal real files of every accepted type, generated in memory for the upload tests."""

import io
import struct
import zipfile
import zlib

from django.core.files.uploadedfile import SimpleUploadedFile

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

_CONTENT_TYPES = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>'
)


def _zip(entries, mimetype=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if mimetype:  # OpenDocument: an uncompressed ``mimetype`` entry first
            archive.writestr(zipfile.ZipInfo("mimetype"), mimetype, zipfile.ZIP_STORED)
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


def _ooxml(main_part: str) -> bytes:
    return _zip(
        [("[Content_Types].xml", _CONTENT_TYPES), ("_rels/.rels", "<x/>"), (main_part, "<w/>")]
    )


def _png() -> bytes:
    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + chunk(b"IEND", b"")
    )


PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
PNG = _png()
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)
WEBP = (
    b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00/\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88"
    b"\xfe\x07\x00"
)

SAMPLES = {
    "pdf": PDF,
    "docx": _ooxml("word/document.xml"),
    "xlsx": _ooxml("xl/workbook.xml"),
    "pptx": _ooxml("ppt/presentation.xml"),
    "odt": _zip([("content.xml", "<x/>")], "application/vnd.oasis.opendocument.text"),
    "ods": _zip([("content.xml", "<x/>")], "application/vnd.oasis.opendocument.spreadsheet"),
    "odp": _zip([("content.xml", "<x/>")], "application/vnd.oasis.opendocument.presentation"),
    "txt": "Plain text, accentué.\n".encode(),
    "md": b"# Title\n\nSome *text* and a [link](https://example.com).\n",
    "csv": b"name,value\nalpha,1\nbeta,2\n",
    "png": PNG,
    "jpg": JPEG,
    "jpeg": JPEG,
    "gif": GIF,
    "webp": WEBP,
    "zip": _zip([("readme.txt", "hello")]),
    "ipynb": b'{\n "cells": [],\n "metadata": {},\n "nbformat": 4,\n "nbformat_minor": 5\n}\n',
    "sql": b"SELECT id, name FROM users WHERE id = 1;\n",
    "py": b"import os\n\n\ndef main():\n    return os.getcwd()\n",
    "json": b'{"a": 1, "b": [1, 2]}\n',
    "yaml": b"a: 1\nb:\n  - x\n",
    "yml": b"key: value\n",
}


def upload(name="guide.pdf", content=PDF, content_type="application/octet-stream"):
    """A Django uploaded file (the declared content type is ignored by the validation)."""
    return SimpleUploadedFile(name, content, content_type=content_type)
