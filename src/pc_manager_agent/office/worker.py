"""Fixed parser subprocess with bounded JSON/bytes IPC; no pickle results or user code."""

from __future__ import annotations

import ctypes
import logging
import multiprocessing
import time
from multiprocessing.connection import Connection
from typing import Any, Literal, cast

from pydantic import Field

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import DocumentFormat, OfficeError, StructuredDocument
from pc_manager_agent.domain.plans import FrozenModel
from pc_manager_agent.tools.manifest import CancellationToken


class ParserRequest(FrozenModel):
    """Fixed parser input header; contains neither a path nor a command."""

    format: DocumentFormat
    limits: OfficeLimits
    delimiter: str = Field(default=",", max_length=1)
    action: Literal["PARSE", "SERIALIZE"] = "PARSE"


def parser_entry(connection: Connection, ready: Any) -> None:
    """Wait for parent resource limits, then parse one bounded frame and exit."""
    logging.disable(logging.CRITICAL)
    try:
        if not ready.wait(10):
            return
        header = ParserRequest.model_validate_json(connection.recv_bytes(16_384))
        data = connection.recv_bytes(50 * 1024**2)
        from pc_manager_agent.office.adapters import parse_document, serialize_document

        if header.action == "PARSE":
            document = parse_document(data, header.format, header.limits, header.delimiter)
            encoded = document.model_dump_json().encode("utf-8")
        else:
            document = StructuredDocument.model_validate_json(connection.recv_bytes(32 * 1024**2))
            if data:
                original = parse_document(data, header.format, header.limits, header.delimiter)
                from pc_manager_agent.domain.office_documents import DocumentSupport

                if original.support is not DocumentSupport.EDITABLE:
                    raise OfficeError("DOCUMENT_READ_ONLY")
            encoded = serialize_document(document, data or None)
        if len(encoded) > 50 * 1024**2:
            raise OfficeError("PARSED_RESULT_LIMIT")
        connection.send_bytes(b"OK")
        connection.send_bytes(encoded)
    except OfficeError as exc:
        connection.send_bytes(b"ERROR")
        connection.send_bytes(exc.code.encode("ascii"))
    except Exception:
        # Never expose parser exceptions: some include PDF streams or cell values.
        try:
            connection.send_bytes(b"ERROR")
            connection.send_bytes(b"DOCUMENT_PARSE_FAILED")
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class BoundedOfficeParser:
    """Start only the project-owned parser and limit its memory before sending content."""

    def __init__(self, limits: OfficeLimits) -> None:
        self.limits = limits

    def parse(
        self,
        data: bytes,
        format_: DocumentFormat,
        cancellation: CancellationToken,
        delimiter: str = ",",
    ) -> StructuredDocument:
        """Return a typed parsed view or fail on cancellation, timeout and resource exhaustion."""
        payload = self._run(data, format_, cancellation, delimiter, None)
        return StructuredDocument.model_validate_json(payload)

    def render(
        self,
        document: StructuredDocument,
        original: bytes | None,
        cancellation: CancellationToken,
    ) -> bytes:
        """Serialize inside the same bounded fixed worker; never hand it filesystem authority."""
        return self._run(
            original or b"", document.format, cancellation, document.delimiter, document
        )

    def _run(
        self,
        data: bytes,
        format_: DocumentFormat,
        cancellation: CancellationToken,
        delimiter: str,
        document: StructuredDocument | None,
    ) -> bytes:
        if len(data) > self.limits.other_bytes or cancellation.is_cancelled:
            raise OfficeError("DOCUMENT_SIZE_LIMIT_OR_CANCELLED")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        ready = context.Event()
        process = context.Process(target=parser_entry, args=(child, ready), daemon=True)
        process.start()
        child.close()
        job: Any = None
        try:
            import win32api
            import win32job

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create_job = cast(Any, kernel.CreateJobObjectW)
            create_job.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            create_job.restype = ctypes.c_void_p
            job = create_job(None, None)
            if not job:
                raise OfficeError("DOCUMENT_PARSER_UNAVAILABLE")
            info = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation
            )
            info["BasicLimitInformation"]["LimitFlags"] = (
                win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            )
            info["BasicLimitInformation"]["ActiveProcessLimit"] = 1
            info["ProcessMemoryLimit"] = self.limits.parser_memory_bytes
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            if process.pid is None:
                raise OfficeError("DOCUMENT_PARSER_UNAVAILABLE")
            handle = win32api.OpenProcess(0x0100 | 0x0001, False, process.pid)
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                win32api.CloseHandle(handle)
            ready.set()
            parent.send_bytes(
                ParserRequest(
                    format=format_,
                    limits=self.limits,
                    delimiter=delimiter,
                    action="PARSE" if document is None else "SERIALIZE",
                )
                .model_dump_json()
                .encode("utf-8")
            )
            parent.send_bytes(data)
            if document is not None:
                encoded_document = document.model_dump_json().encode("utf-8")
                if len(encoded_document) > 32 * 1024**2:
                    raise OfficeError("DOCUMENT_SERIALIZATION_LIMIT")
                parent.send_bytes(encoded_document)
            deadline = time.monotonic() + self.limits.parse_timeout_seconds
            while not parent.poll(0.05):
                if cancellation.is_cancelled:
                    raise OfficeError("DOCUMENT_CANCELLED")
                if time.monotonic() >= deadline:
                    raise OfficeError("DOCUMENT_PARSE_TIMEOUT")
                if not process.is_alive():
                    raise OfficeError("DOCUMENT_PARSER_INTERRUPTED")
            status = parent.recv_bytes(16)
            payload = parent.recv_bytes(50 * 1024**2)
            if status != b"OK":
                code = payload.decode("ascii")
                if not code.replace("_", "").isalnum() or len(code) > 100:
                    code = "DOCUMENT_PARSE_FAILED"
                raise OfficeError(code)
            if cancellation.is_cancelled:
                raise OfficeError("DOCUMENT_CANCELLED")
            return payload
        except (EOFError, OSError) as exc:
            raise OfficeError("DOCUMENT_PARSER_UNAVAILABLE") from exc
        finally:
            parent.close()
            if job is not None:
                win32api.CloseHandle(job)
            if process.is_alive():
                # This is only our private disposable parser, never Word/Excel or another app.
                process.terminate()
            process.join(timeout=2)
            process.close()
