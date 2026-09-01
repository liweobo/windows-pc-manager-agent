from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.domain.browser_downloads import BrowserDownloadPreview
from pc_manager_agent.safety.browser.downloads import (
    BrowserDownloadManager,
    BrowserDownloadPolicy,
    BrowserDownloadPolicyError,
)


def _preview(destination: Path, *, filename: str = "report.pdf") -> BrowserDownloadPreview:
    return BrowserDownloadPreview(
        session_id=uuid4(),
        page_id=uuid4(),
        navigation_id=uuid4(),
        element_id=uuid4(),
        source_url=f"https://example.com/{filename}",
        source_origin="https://example.com",
        suggested_filename=filename,
        declared_mime_type="application/pdf",
        destination=destination / filename,
        max_size_bytes=1024,
    )


@pytest.mark.parametrize(
    "filename",
    ["../evil.pdf", "C:evil.pdf", "CON.pdf", "safe.pdf.exe", "bad\u202eexe.pdf", "x.zip"],
)
def test_untrusted_filenames_are_blocked(filename: str) -> None:
    with pytest.raises(BrowserDownloadPolicyError):
        BrowserDownloadPolicy().validate_filename(filename)


def test_mime_and_magic_must_both_match(tmp_path: Path) -> None:
    policy = BrowserDownloadPolicy()
    with pytest.raises(BrowserDownloadPolicyError, match="MIME_MISMATCH"):
        policy.validate_declared("report.pdf", "application/x-msdownload", 10, max_size_bytes=100)
    temporary = tmp_path / "bad.download"
    temporary.write_bytes(b"MZ dangerous")
    with pytest.raises(BrowserDownloadPolicyError, match="MAGIC_MISMATCH"):
        policy.validate_completed(_preview(tmp_path), temporary)


def test_commit_no_overwrite_and_conditional_full_rollback(tmp_path: Path) -> None:
    temporary = tmp_path / "temporary.download"
    temporary.write_bytes(b"%PDF-1.7\n%%EOF")
    destination = tmp_path / "downloads"
    preview = _preview(destination)
    manager = BrowserDownloadManager()
    identity, recovery = manager.commit(preview, temporary)
    assert identity.path.read_bytes().startswith(b"%PDF-")
    assert len(identity.sha256) == 64
    with pytest.raises(FileExistsError):
        manager.commit(preview, temporary)
    recovered = manager.rollback(recovery)
    assert recovered.exists()
    assert not identity.path.exists()


def test_changed_download_cannot_be_rolled_back(tmp_path: Path) -> None:
    temporary = tmp_path / "temporary.download"
    temporary.write_bytes(b"%PDF-1.7\n%%EOF")
    identity, recovery = BrowserDownloadManager().commit(_preview(tmp_path / "out"), temporary)
    identity.path.write_bytes(b"%PDF-1.7\nDIFF!")
    with pytest.raises(BrowserDownloadPolicyError, match="ROLLBACK_CONFLICT"):
        BrowserDownloadManager().rollback(recovery)


def test_staged_artifacts_are_retained_without_delete_or_path_expansion(tmp_path: Path) -> None:
    staged = tmp_path / "opaque.download"
    staged.write_bytes(b"%PDF-1.7\n%%EOF")
    manager = BrowserDownloadManager()
    retained = manager.retain_staged(staged, reason="validated-staging-copy")
    assert retained.parent == tmp_path / ".pc-manager-recovery"
    assert retained.read_bytes().startswith(b"%PDF-")
    assert not staged.exists()

    another = tmp_path / "another.download"
    another.write_bytes(b"safe")
    with pytest.raises(BrowserDownloadPolicyError, match="REASON_BLOCKED"):
        manager.retain_staged(another, reason="../outside")
    assert another.exists()


def test_filename_size_temp_and_recovery_conflict_branches(tmp_path: Path) -> None:
    policy = BrowserDownloadPolicy()
    for name in ("", "x" * 256 + ".pdf", "bad\x00.pdf", "bad?.pdf", "name. "):
        with pytest.raises(BrowserDownloadPolicyError):
            policy.validate_filename(name)
    with pytest.raises(BrowserDownloadPolicyError, match="SIZE_BLOCKED"):
        policy.validate_declared("x.pdf", "application/pdf", 2, max_size_bytes=1)
    with pytest.raises(BrowserDownloadPolicyError, match="SIZE_BLOCKED"):
        policy.validate_declared("x.pdf", "application/pdf", None, max_size_bytes=0)
    with pytest.raises(BrowserDownloadPolicyError, match="TEMP_UNAVAILABLE"):
        policy.validate_completed(_preview(tmp_path), tmp_path / "missing")
    directory = tmp_path / "directory.download"
    directory.mkdir()
    with pytest.raises(BrowserDownloadPolicyError, match="TEMP_NOT_REGULAR"):
        policy.validate_completed(_preview(tmp_path), directory)
    large = tmp_path / "large.download"
    large.write_bytes(b"%PDF-" + b"x" * 1024)
    with pytest.raises(BrowserDownloadPolicyError, match="SIZE_BLOCKED"):
        policy.validate_completed(_preview(tmp_path), large)

    temporary = tmp_path / "ok.download"
    temporary.write_bytes(b"%PDF-1.7\n%%EOF")
    identity, recovery = BrowserDownloadManager().commit(_preview(tmp_path / "out"), temporary)
    recovery.recovery_path.parent.mkdir(parents=True)
    recovery.recovery_path.write_text("occupied", encoding="utf-8")
    with pytest.raises(BrowserDownloadPolicyError, match="RECOVERY_CONFLICT"):
        BrowserDownloadManager().rollback(recovery)
    assert identity.path.exists()


@pytest.mark.parametrize(
    ("filename", "mime", "head"),
    [
        ("x.pdf", "application/pdf", b"%PDF-1.7"),
        (
            "x.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK\x03\x04",
        ),
        (
            "x.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK\x03\x04",
        ),
        ("x.png", "image/png", b"\x89PNG\r\n\x1a\n"),
        ("x.jpg", "image/jpeg", b"\xff\xd8\xff"),
        ("x.jpeg", "image/jpeg", b"\xff\xd8\xff"),
        ("x.gif", "image/gif", b"GIF89a"),
        ("x.webp", "image/webp", b"RIFF0000WEBP"),
        ("x.txt", "text/plain", b"plain text"),
        ("x.csv", "text/csv", b"a,b\n1,2"),
        ("x.json", "application/json", b'{"a": 1}'),
    ],
)
def test_all_allowlisted_document_magic_types(
    tmp_path: Path,
    filename: str,
    mime: str,
    head: bytes,
) -> None:
    temporary = tmp_path / f"{filename}.download"
    temporary.write_bytes(head)
    preview = BrowserDownloadPreview(
        session_id=uuid4(),
        page_id=uuid4(),
        navigation_id=uuid4(),
        element_id=uuid4(),
        source_url=f"https://example.com/{filename}",
        source_origin="https://example.com",
        suggested_filename=filename,
        declared_mime_type=mime,
        destination=tmp_path / "out" / filename,
        max_size_bytes=1024,
    )
    policy = BrowserDownloadPolicy()
    policy.validate_declared(filename, mime + "; charset=utf-8", len(head), max_size_bytes=1024)
    assert policy.validate_completed(preview, temporary) == mime


def test_binary_nul_is_not_accepted_as_text(tmp_path: Path) -> None:
    temporary = tmp_path / "binary.download"
    temporary.write_bytes(b"bad\x00text")
    preview = BrowserDownloadPreview(
        session_id=uuid4(),
        page_id=uuid4(),
        navigation_id=uuid4(),
        element_id=uuid4(),
        source_url="https://example.com/x.txt",
        source_origin="https://example.com",
        suggested_filename="x.txt",
        declared_mime_type="text/plain",
        destination=tmp_path / "x.txt",
        max_size_bytes=1024,
    )
    with pytest.raises(BrowserDownloadPolicyError, match="MAGIC_MISMATCH"):
        BrowserDownloadPolicy().validate_completed(preview, temporary)
