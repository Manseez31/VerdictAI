"""Upload security: content-based type validation, integrity hashing, AV hook.

WHY THIS EXISTS
---------------
`/extract-document` previously trusted the *filename extension* and the
client-supplied Content-Type. Both are attacker-controlled. A file named
`case.pdf` can be anything at all.

DEFENSE
-------
  1. Magic-byte validation — decide the real type from file CONTENT, and require
     it to match the claimed extension. `evil.exe` renamed `case.pdf` is rejected.
  2. Size limits + zip-bomb guard for DOCX (a DOCX is a zip; a 1 MB upload can
     expand to gigabytes).
  3. SHA-256 document hash — provenance/integrity. The hash is recorded in the
     audit log so a stored analysis can be tied to the exact bytes analysed
     (document hash validation / RAG-poisoning provenance).
  4. Malware scan HOOK — a pluggable interface. Ships with a no-op scanner and
     an EICAR check; wire ClamAV/S3 AV in production via `set_malware_scanner`.
"""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from typing import Callable, Optional

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024          # 10 MB
MAX_DECOMPRESSED_BYTES = 100 * 1024 * 1024   # zip-bomb ceiling for DOCX

# EICAR antivirus test string — lets us prove the scanner hook actually fires.
_EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class UnsafeUpload(ValueError):
    """Raised when an upload fails a security check."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- magic bytes ------------------------------------------------------------

def sniff_type(data: bytes) -> Optional[str]:
    """Determine the real file type from content. Returns 'pdf'|'docx'|'txt'|None."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        # A zip — a DOCX must contain the Word document part.
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if "word/document.xml" in names:
                    return "docx"
        except zipfile.BadZipFile:
            return None
        return None
    # Treat as text only if it decodes cleanly and has no NUL bytes.
    if b"\x00" in data[:4096]:
        return None
    try:
        data[:4096].decode("utf-8")
        return "txt"
    except UnicodeDecodeError:
        return None


# --- malware scanning hook --------------------------------------------------

MalwareScanner = Callable[[bytes], bool]   # returns True if malicious


def _default_scanner(data: bytes) -> bool:
    """No real AV engine is bundled. Detects the EICAR test file so the hook is
    provably wired; replace in production via set_malware_scanner()."""
    return _EICAR in data


_scanner: MalwareScanner = _default_scanner


def set_malware_scanner(scanner: MalwareScanner) -> None:
    """Install a production scanner (e.g. ClamAV via pyclamd)."""
    global _scanner
    _scanner = scanner
    logger.info("Malware scanner installed: %s", getattr(scanner, "__name__", scanner))


def _zip_bomb(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = sum(i.file_size for i in z.infolist())
            return total > MAX_DECOMPRESSED_BYTES
    except zipfile.BadZipFile:
        return False


def validate_upload(filename: str, content_type: str, data: bytes) -> dict:
    """Run every upload check. Raises UnsafeUpload on failure.

    Returns {'kind', 'sha256', 'size'} on success.
    """
    if not data:
        raise UnsafeUpload("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UnsafeUpload("File too large (max 10 MB).")

    # 1. Content-based type (never trust the extension/Content-Type).
    real = sniff_type(data)
    if real is None:
        raise UnsafeUpload("Unsupported or corrupt file. Only PDF, DOCX, or TXT are accepted.")

    # 2. Claimed vs actual — catches `malware.exe` renamed to `case.pdf`.
    name = (filename or "").lower()
    claimed = (
        "pdf" if name.endswith(".pdf")
        else "docx" if name.endswith(".docx")
        else "txt" if name.endswith(".txt")
        else None
    )
    if claimed and claimed != real:
        raise UnsafeUpload(
            f"File content does not match its .{claimed} extension (detected: {real}). Rejected."
        )

    # 3. Zip-bomb guard.
    if real == "docx" and _zip_bomb(data):
        raise UnsafeUpload("Archive expands to an unsafe size. Rejected.")

    # 4. Malware scan hook.
    try:
        if _scanner(data):
            logger.warning("Malware scanner flagged upload %r", filename)
            raise UnsafeUpload("The file was flagged by the malware scanner.")
    except UnsafeUpload:
        raise
    except Exception:
        # Fail CLOSED: if the scanner is broken we do not silently accept files.
        logger.exception("Malware scanner error — rejecting upload")
        raise UnsafeUpload("The file could not be security-scanned. Please try again.")

    digest = sha256_hex(data)
    logger.info("Upload accepted: kind=%s size=%d sha256=%s", real, len(data), digest[:16])
    return {"kind": real, "sha256": digest, "size": len(data)}
