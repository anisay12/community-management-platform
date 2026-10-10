import hashlib

import pytest

from core.errors import DomainError
from documents.validation import (
    FILENAME_MAX_LENGTH,
    OOXML,
    sanitize_filename,
    validate_upload,
)

from .samples import PDF, PNG, SAMPLES, upload


@pytest.mark.parametrize("extension", sorted(SAMPLES))
def test_accepted_types(extension):
    content = SAMPLES[extension]
    result = validate_upload(upload(f"sample.{extension}", content))
    assert result.extension == extension
    assert result.filename == f"sample.{extension}"
    assert result.size == len(content)
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert result.mime_type


@pytest.mark.parametrize("extension", sorted(OOXML))
def test_office_documents_store_their_specific_type(extension):
    result = validate_upload(upload(f"office.{extension}", SAMPLES[extension]))
    assert result.mime_type == OOXML[extension]


def test_detected_types():
    assert validate_upload(upload("a.pdf", PDF)).mime_type == "application/pdf"
    assert validate_upload(upload("a.png", PNG)).mime_type == "image/png"
    assert validate_upload(upload("a.JPG", SAMPLES["jpg"])).mime_type == "image/jpeg"


def test_extension_is_case_insensitive():
    result = validate_upload(upload("REPORT.PDF", PDF))
    assert result.extension == "pdf"
    assert result.filename == "REPORT.PDF"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("logo.svg", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'),
        ("setup.exe", b"MZ\x90\x00\x03" + b"\x00" * 100),
        ("page.html", b"<!DOCTYPE html><html><body>x</body></html>"),
        ("page.htm", b"<html></html>"),
        ("script.js", b"alert(1)\n"),
        ("run.sh", b"#!/bin/sh\necho hi\n"),
        ("noextension", b"hello"),
        (".pdf", PDF),
    ],
)
def test_refused_extensions(name, content):
    with pytest.raises(DomainError) as error:
        validate_upload(upload(name, content))
    assert error.value.code == "extension_refused"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("image.pdf", PNG),  # a PNG renamed .pdf
        ("document.png", PDF),
        ("notes.txt", b"<!DOCTYPE html><html><body><script>x</script></body></html>"),
        ("drawing.txt", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'),
        ("program.pdf", b"MZ\x90\x00\x03" + b"\x00" * 100),
        ("archive.docx", PDF),
        ("sheet.xlsx", SAMPLES["docx"]),
        ("binary.csv", PNG),
        ("photo.jpg", PNG),
    ],
)
def test_type_mismatch(name, content):
    with pytest.raises(DomainError) as error:
        validate_upload(upload(name, content))
    assert error.value.code == "type_mismatch"


def test_empty_file_refused():
    with pytest.raises(DomainError) as error:
        validate_upload(upload("empty.txt", b""))
    assert error.value.code == "file_empty"


def test_size_limit(settings):
    settings.DOCUMENT_MAX_UPLOAD_BYTES = 100
    assert validate_upload(upload("ok.txt", b"x" * 100)).size == 100
    with pytest.raises(DomainError) as error:
        validate_upload(upload("big.txt", b"x" * 101))
    assert error.value.code == "file_too_large"


def test_size_counted_on_content_not_declared_size(settings):
    settings.DOCUMENT_MAX_UPLOAD_BYTES = 100
    file = upload("big.txt", b"x" * 500)
    file.size = 10  # the declared size lies
    with pytest.raises(DomainError) as error:
        validate_upload(file)
    assert error.value.code == "file_too_large"


def test_hash_streamed_in_chunks_and_file_rewound(monkeypatch):
    import documents.validation as validation

    monkeypatch.setattr(validation, "CHUNK_SIZE", 7)
    content = b"0123456789" * 50
    file = upload("data.txt", content)
    calls = []
    original = file.chunks

    def chunks(chunk_size=None):
        calls.append(chunk_size)
        return original(chunk_size)

    file.chunks = chunks
    result = validate_upload(file)
    assert calls == [7]
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert file.read() == content  # positioned at the start again


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\windows\\system32\\evil.pdf", "evil.pdf"),
        ("/absolute/path/report.pdf", "report.pdf"),
        ("C:\\Users\\me\\report.pdf", "report.pdf"),
        ("rep\x00ort\n\t.pdf", "report.pdf"),
        ("  ..hidden.pdf ", "hidden.pdf"),
        ('a<b>c:d"e|f?g*h.pdf', "a_b_c_d_e_f_g_h.pdf"),
        ("multiple   spaces.pdf", "multiple spaces.pdf"),
        ("Résumé.pdf", "Résumé.pdf"),
        ("..pdf", "pdf"),  # leading dots dropped: no extension left
        ("", "file"),
        ("archive.tar.gz", "archive.tar.gz"),
    ],
)
def test_sanitize_filename(name, expected):
    assert sanitize_filename(name) == expected


def test_long_filename_keeps_extension():
    name = sanitize_filename("x" * 500 + ".pdf")
    assert len(name) == FILENAME_MAX_LENGTH
    assert name.endswith(".pdf")


def test_path_traversal_name_validated_on_base_name():
    result = validate_upload(upload("../../secret/../report.pdf", PDF))
    assert result.filename == "report.pdf"
    assert "/" not in result.filename
