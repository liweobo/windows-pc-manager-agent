"""Static package, formula and disclosure guards. Nothing here evaluates content."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from pathlib import PurePosixPath

# Return type only. All parsing below uses defusedxml with DTD/entities disabled.
from xml.etree.ElementTree import Element  # nosec B405

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from pc_manager_agent.config.office import OfficeLimits
from pc_manager_agent.domain.office_documents import DocumentFormat, OfficeError


def safe_xml(data: bytes) -> Element:
    """Parse bounded XML with entities, DTD and external access forbidden."""
    if len(data) > 25 * 1024**2:
        raise OfficeError("XML_SIZE_LIMIT")
    try:
        root = ElementTree.fromstring(
            data, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (ElementTree.ParseError, DefusedXmlException, ValueError) as exc:
        raise OfficeError("UNSAFE_OR_MALFORMED_XML") from exc
    pending = [(root, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 100 or count > 500_000:
            raise OfficeError("XML_STRUCTURE_LIMIT")
        pending.extend((child, depth + 1) for child in node)
    return root


def inspect_package(data: bytes, format_: DocumentFormat, limits: OfficeLimits) -> tuple[str, ...]:
    """Validate the whole ZIP directory and static XML before a format library sees it."""
    warnings: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > limits.zip_entries:
                raise OfficeError("ZIP_ENTRY_LIMIT")
            total = 0
            names: set[str] = set()
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                folded = name.casefold()
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in name
                    or ":" in name
                    or folded in names
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or entry.flag_bits & 1
                ):
                    raise OfficeError("UNSAFE_PACKAGE_ENTRY")
                names.add(folded)
                total += entry.file_size
                if (
                    total > limits.expanded_bytes
                    or entry.file_size > max(1, entry.compress_size) * limits.compression_ratio
                ):
                    raise OfficeError("ZIP_EXPANSION_LIMIT")
                if any(
                    part in folded
                    for part in (
                        "vbaproject",
                        "macrosheet",
                        "activex",
                        "embeddings/",
                        "externallinks/",
                        "connections.xml",
                        "querytables/",
                        "pivot",
                        "drawings/",
                        "charts/",
                        "_xmlsignatures/",
                        "comments",
                        "footnotes",
                        "endnotes",
                        "header",
                        "footer",
                    )
                ):
                    warnings.add("LIMITED_EDIT_SUPPORT")
                if "vba" in folded or "macrosheet" in folded:
                    warnings.add("MACRO_ENABLED_DOCUMENT")
                if folded.endswith((".xml", ".rels")):
                    xml = safe_xml(archive.read(entry))
                    sections = 0
                    for node in xml.iter():
                        tag = node.tag.rsplit("}", 1)[-1]
                        if node.attrib.get("TargetMode", "").casefold() == "external":
                            warnings.add("EXTERNAL_CONTENT")
                        if tag in {
                            "ins",
                            "del",
                            "fldChar",
                            "instrText",
                            "altChunk",
                            "object",
                            "drawing",
                            "pict",
                            "sdt",
                            "documentProtection",
                            "sheetProtection",
                            "workbookProtection",
                            "gridSpan",
                            "vMerge",
                            "customSheetView",
                            "oleObject",
                            "definedName",
                            "extLst",
                            "customXml",
                            "dataValidation",
                            "hyperlink",
                        } and not (tag == "workbookProtection" and not node.attrib):
                            warnings.add(
                                "PROTECTED_DOCUMENT"
                                if "Protection" in tag
                                else "LIMITED_EDIT_SUPPORT"
                            )
                        if tag == "sectPr":
                            sections += 1
                        if format_ in {DocumentFormat.XLSX, DocumentFormat.XLSM} and tag == "r":
                            warnings.add("LIMITED_EDIT_SUPPORT")
                    if sections > 1:
                        warnings.add("LIMITED_EDIT_SUPPORT")
            if "[content_types].xml" not in names or "_rels/.rels" not in names:
                raise OfficeError("INVALID_OFFICE_PACKAGE")
            types = archive.read("[Content_Types].xml").decode("utf-8-sig")
            expected = (
                "word/"
                if format_ in {DocumentFormat.DOCX, DocumentFormat.DOCM}
                else "xl/"
                if format_ in {DocumentFormat.XLSX, DocumentFormat.XLSM}
                else "ppt/"
            )
            if not any(name.startswith(expected) for name in names):
                raise OfficeError("FORMAT_MISMATCH")
            macro = "macroenabled" in types.casefold() or "MACRO_ENABLED_DOCUMENT" in warnings
            if macro or format_ in {DocumentFormat.DOCM, DocumentFormat.XLSM, DocumentFormat.PPTM}:
                warnings.add("MACRO_ENABLED_DOCUMENT")
            if macro and format_ in {DocumentFormat.DOCX, DocumentFormat.XLSX, DocumentFormat.PPTX}:
                raise OfficeError("DISGUISED_MACRO_PACKAGE")
    except OfficeError:
        raise
    except (zipfile.BadZipFile, KeyError, UnicodeError, OSError, RuntimeError) as exc:
        raise OfficeError("MALFORMED_OFFICE_PACKAGE") from exc
    return tuple(sorted(warnings))


def require_safe_formula(value: str) -> None:
    """Allow only simple local arithmetic/reducer formulas; no links or named code."""
    upper = value.upper()
    if len(upper) > 500 or not re.fullmatch(r"=[A-Z0-9$():,+*/.\- ]+", upper):
        raise OfficeError("UNSUPPORTED_FORMULA")
    for token in re.findall(r"[A-Z]+[0-9]*", upper):
        if token not in {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "ROUND"} and not re.fullmatch(
            r"[A-Z]{1,3}[1-9][0-9]{0,6}", token
        ):
            raise OfficeError("UNSUPPORTED_FORMULA")


def safe_spreadsheet_text(value: str) -> str:
    """Escape formula-like CSV text, including leading Unicode/control whitespace."""
    normalized = unicodedata.normalize("NFKC", value)
    offset = 0
    while offset < len(normalized) and (
        normalized[offset].isspace() or normalized[offset] in "\ufeff\u200b"
    ):
        offset += 1
    meaningful = normalized[offset:]
    if meaningful.startswith(("=", "+", "-", "@")) or normalized.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def require_disclosable(text: str) -> None:
    """Block known secret/credential forms; this is not a complete DLP claim."""
    patterns = (
        r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----",
        r"\b(?:sk|ghp|gho|github_pat)[-_][A-Za-z0-9_-]{16,}",
        r"(?i)\b(?:password|passwd|api[_ -]?key|access[_ -]?token|cookie|authorization)"
        r"\s*[:=]\s*\S+",
        r"(?i)\bBearer\s+[A-Za-z0-9_.-]+",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        raise OfficeError("SENSITIVE_CONTENT_BLOCKED")
