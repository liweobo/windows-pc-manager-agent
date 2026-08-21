"""Offline Authenticode verification and signer-name extraction through Win32 APIs."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any

from pc_manager_agent.domain.vendor_uninstall import (
    VendorAuthenticodeEvidence,
    VendorAuthenticodeStatus,
)

_CERT_QUERY_OBJECT_FILE = 1
_CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED = 1 << 10
_CERT_QUERY_FORMAT_FLAG_BINARY = 1 << 1
_CMSG_SIGNER_INFO_PARAM = 6
_X509_ASN_ENCODING = 0x00000001
_PKCS_7_ASN_ENCODING = 0x00010000
_CERT_FIND_SUBJECT_CERT = 0x000B0000
_CERT_NAME_SIMPLE_DISPLAY_TYPE = 4
_CERT_NAME_ATTR_TYPE = 3
_WTD_UI_NONE = 2
_WTD_REVOKE_NONE = 0
_WTD_CHOICE_FILE = 1
_WTD_STATEACTION_IGNORE = 0
_WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000


class _Guid(ctypes.Structure):
    _fields_ = (
        ("data1", ctypes.c_ulong),
        ("data2", ctypes.c_ushort),
        ("data3", ctypes.c_ushort),
        ("data4", ctypes.c_ubyte * 8),
    )


class _WinTrustFileInfo(ctypes.Structure):
    _fields_ = (
        ("cbStruct", ctypes.c_ulong),
        ("pcwszFilePath", ctypes.c_wchar_p),
        ("hFile", ctypes.c_void_p),
        ("pgKnownSubject", ctypes.c_void_p),
    )


class _WinTrustData(ctypes.Structure):
    _fields_ = (
        ("cbStruct", ctypes.c_ulong),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", ctypes.c_ulong),
        ("fdwRevocationChecks", ctypes.c_ulong),
        ("dwUnionChoice", ctypes.c_ulong),
        ("pFile", ctypes.POINTER(_WinTrustFileInfo)),
        ("dwStateAction", ctypes.c_ulong),
        ("hWVTStateData", ctypes.c_void_p),
        ("pwszURLReference", ctypes.c_wchar_p),
        ("dwProvFlags", ctypes.c_ulong),
        ("dwUIContext", ctypes.c_ulong),
        ("pSignatureSettings", ctypes.c_void_p),
    )


class _Blob(ctypes.Structure):
    _fields_ = (("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte)))


class _AlgorithmIdentifier(ctypes.Structure):
    _fields_ = (
        ("pszObjId", ctypes.c_char_p),
        ("Parameters", _Blob),
    )


class _CryptAttributes(ctypes.Structure):
    _fields_ = (("cAttr", ctypes.c_ulong), ("rgAttr", ctypes.c_void_p))


class _SignerInfo(ctypes.Structure):
    _fields_ = (
        ("dwVersion", ctypes.c_ulong),
        ("Issuer", _Blob),
        ("SerialNumber", _Blob),
        ("HashAlgorithm", _AlgorithmIdentifier),
        ("HashEncryptionAlgorithm", _AlgorithmIdentifier),
        ("EncryptedHash", _Blob),
        ("AuthAttrs", _CryptAttributes),
        ("UnauthAttrs", _CryptAttributes),
    )


class _CertInfo(ctypes.Structure):
    _fields_ = (
        ("dwVersion", ctypes.c_ulong),
        ("SerialNumber", _Blob),
        ("SignatureAlgorithm", _AlgorithmIdentifier),
        ("Issuer", _Blob),
        ("NotBefore", ctypes.c_ulonglong),
        ("NotAfter", ctypes.c_ulonglong),
        ("Subject", _Blob),
        ("SubjectPublicKeyInfo", ctypes.c_byte * 40),
        ("IssuerUniqueId", _Blob),
        ("SubjectUniqueId", _Blob),
        ("cExtension", ctypes.c_ulong),
        ("rgExtension", ctypes.c_void_p),
    )


class WindowsAuthenticodeVerifier:
    """Verify an embedded signature with cached trust only and read its signer identity."""

    def verify(self, path: Path) -> VendorAuthenticodeEvidence:
        """Return VALID only when offline WinVerifyTrust and signer extraction both succeed."""
        if os.name != "nt":
            return VendorAuthenticodeEvidence(
                status=VendorAuthenticodeStatus.UNKNOWN,
                error_code="platform_unsupported",
            )
        trust_status = _verify_trust(path)
        if trust_status != 0:
            return VendorAuthenticodeEvidence(
                status=VendorAuthenticodeStatus.INVALID,
                error_code=f"winverifytrust_{trust_status & 0xFFFFFFFF:08x}",
            )
        try:
            subject, organization = _read_signer(path)
        except OSError as exc:
            return VendorAuthenticodeEvidence(
                status=VendorAuthenticodeStatus.UNKNOWN,
                error_code=f"signer_read_{getattr(exc, 'winerror', None) or exc.errno or 0}",
            )
        if not subject:
            return VendorAuthenticodeEvidence(
                status=VendorAuthenticodeStatus.UNKNOWN,
                error_code="signer_identity_missing",
            )
        return VendorAuthenticodeEvidence(
            status=VendorAuthenticodeStatus.VALID,
            signer_subject=subject,
            signer_organization=organization,
        )


def _verify_trust(path: Path) -> int:
    """Call WinVerifyTrust with no UI and no network retrieval."""
    action = _Guid(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    file_info = _WinTrustFileInfo(ctypes.sizeof(_WinTrustFileInfo), str(path), None, None)
    data = _WinTrustData()
    data.cbStruct = ctypes.sizeof(_WinTrustData)
    data.dwUIChoice = _WTD_UI_NONE
    data.fdwRevocationChecks = _WTD_REVOKE_NONE
    data.dwUnionChoice = _WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = _WTD_STATEACTION_IGNORE
    data.dwProvFlags = _WTD_CACHE_ONLY_URL_RETRIEVAL
    function = ctypes.WinDLL("wintrust", use_last_error=True).WinVerifyTrust
    function.argtypes = (ctypes.c_void_p, ctypes.POINTER(_Guid), ctypes.POINTER(_WinTrustData))
    function.restype = ctypes.c_long
    return int(function(ctypes.c_void_p(-1), ctypes.byref(action), ctypes.byref(data)))


def _read_signer(path: Path) -> tuple[str | None, str | None]:
    """Read the embedded PKCS#7 signer certificate without contacting a network."""
    crypt32: Any = ctypes.WinDLL("crypt32", use_last_error=True)
    store = ctypes.c_void_p()
    message = ctypes.c_void_p()
    encoding = ctypes.c_ulong()
    content = ctypes.c_ulong()
    file_format = ctypes.c_ulong()
    context = ctypes.c_void_p()
    query = crypt32.CryptQueryObject
    query.argtypes = (
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    query.restype = ctypes.c_int
    path_buffer = ctypes.create_unicode_buffer(str(path))
    if not query(
        _CERT_QUERY_OBJECT_FILE,
        ctypes.cast(path_buffer, ctypes.c_void_p),
        _CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBED,
        _CERT_QUERY_FORMAT_FLAG_BINARY,
        0,
        ctypes.byref(encoding),
        ctypes.byref(content),
        ctypes.byref(file_format),
        ctypes.byref(store),
        ctypes.byref(message),
        ctypes.byref(context),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    certificate = ctypes.c_void_p()
    try:
        size = ctypes.c_ulong(0)
        if not crypt32.CryptMsgGetParam(
            message, _CMSG_SIGNER_INFO_PARAM, 0, None, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not crypt32.CryptMsgGetParam(
            message, _CMSG_SIGNER_INFO_PARAM, 0, buffer, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        signer = ctypes.cast(buffer, ctypes.POINTER(_SignerInfo)).contents
        cert_info = _CertInfo()
        cert_info.Issuer = signer.Issuer
        cert_info.SerialNumber = signer.SerialNumber
        find_certificate = crypt32.CertFindCertificateInStore
        find_certificate.argtypes = (
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        find_certificate.restype = ctypes.c_void_p
        certificate = ctypes.c_void_p(
            find_certificate(
                store,
                _X509_ASN_ENCODING | _PKCS_7_ASN_ENCODING,
                0,
                _CERT_FIND_SUBJECT_CERT,
                ctypes.byref(cert_info),
                None,
            )
        )
        if not certificate.value:
            raise ctypes.WinError(ctypes.get_last_error())
        subject = _certificate_name(crypt32, certificate, _CERT_NAME_SIMPLE_DISPLAY_TYPE)
        organization_oid = ctypes.c_char_p(b"2.5.4.10")
        organization = _certificate_name(
            crypt32,
            certificate,
            _CERT_NAME_ATTR_TYPE,
            ctypes.cast(organization_oid, ctypes.c_void_p),
        )
        return subject, organization
    finally:
        if certificate.value:
            crypt32.CertFreeCertificateContext(certificate)
        if message.value:
            crypt32.CryptMsgClose(message)
        if store.value:
            crypt32.CertCloseStore(store, 0)


def _certificate_name(
    crypt32: Any,
    certificate: ctypes.c_void_p,
    name_type: int,
    parameter: ctypes.c_void_p | None = None,
) -> str | None:
    """Read one bounded certificate display name."""
    get_name = crypt32.CertGetNameStringW
    get_name.restype = ctypes.c_ulong
    size = int(get_name(certificate, name_type, 0, parameter, None, 0))
    if size <= 1 or size > 2_048:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    if not get_name(certificate, name_type, 0, parameter, buffer, size):
        return None
    value = buffer.value.strip()
    return value or None
