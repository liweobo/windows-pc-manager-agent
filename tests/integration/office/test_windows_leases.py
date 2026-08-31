from pathlib import Path

import pytest

from pc_manager_agent.domain.office_documents import DocumentFormat, OfficeError
from pc_manager_agent.platform_support.windows.office_files import WindowsOfficeFiles
from pc_manager_agent.tools.manifest import CancellationToken


def test_handle_identity_and_no_replace_commit(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"original")
    files = WindowsOfficeFiles()
    with files.pin_parents(source), files.open(source, mutable=True) as lease:
        data = lease.read(100, CancellationToken())
        before = lease.identity(data, DocumentFormat.TXT)
        assert before.state.size_bytes == 8
        occupied = tmp_path / "occupied.txt"
        occupied.write_bytes(b"do not overwrite")
        with pytest.raises(OfficeError, match="CONFLICT"):
            lease.rename_absent(occupied)
        assert occupied.read_bytes() == b"do not overwrite"
        moved = tmp_path / "moved.txt"
        lease.rename_absent(moved)
        after = lease.identity(data, DocumentFormat.TXT)
        assert before.state.file_id == after.state.file_id
        assert lease.read(100, CancellationToken()) == b"original"
    assert not source.exists()
    assert moved.read_bytes() == b"original"


def test_new_file_and_existing_write_lock(tmp_path: Path) -> None:
    files = WindowsOfficeFiles()
    source = tmp_path / "created.txt"
    with files.pin_parents(source), files.open(source, create=True) as lease:
        lease.write_new(b"new")
        assert lease.read(100, CancellationToken()) == b"new"
        with pytest.raises(OfficeError), files.open(source, mutable=True):
            pass
    with files.open(source) as lease, pytest.raises(OfficeError, match="SIZE_LIMIT"):
        lease.read(1, CancellationToken())


def test_cancellation_before_read(tmp_path: Path) -> None:
    source = tmp_path / "text.txt"
    source.write_bytes(b"content")
    token = CancellationToken()
    token.cancel()
    with WindowsOfficeFiles().open(source) as lease, pytest.raises(OfficeError, match="CANCELLED"):
        lease.read(100, token)
