"""The upload pipeline and file delivery (docs/02 §9, 03 §5.5, 04 §11; 05 DOC-1..8).

UPLOAD, in this order:
  1. size limit while reading (never more than the limit + 1 byte in memory) -> 413
  2. the REAL type from the first bytes (magic numbers), never the name or the
     browser's Content-Type -> 415 for anything else (no SVG, HTML, executables)
  3. per-category allowlist (photos: images only)
  4. images are decoded and RE-ENCODED with Pillow: metadata (EXIF, GPS) and anything
     hidden after the image data are dropped; a file that doesn't decode is rejected
     PDFs must start with "%PDF-" and end with "%%EOF" (Q66)
  5. identity documents are ENCRYPTED (AES-256-GCM, associated data org|file|category)
     before they touch the disk (Q65)
  6. random storage key, atomic write, metadata row + audit row in the caller's transaction

DELIVERY: standard files through 60-second signed links bound to the user and session;
restricted (identity) files never get links: only an authenticated, audited request.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError
from sqlalchemy import ColumnElement, exists, or_, select, text
from sqlalchemy.orm import Session

from app.core import audit, crypto
from app.core.config import get_settings
from app.core.errors import ProblemError
from app.core.permissions import Principal
from app.core.storage import get_storage
from app.modules.files.models import File

MAX_BYTES = 10 * 1024 * 1024  # 04 §11.1
PHOTO_MAX_BYTES = 5 * 1024 * 1024
LINK_TTL_SECONDS = 60
IMAGE_TYPES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
CATEGORY_TYPES: dict[str, set[str]] = {
    "student_photo": set(IMAGE_TYPES),
    "identity_document": {*IMAGE_TYPES, "application/pdf"},
    "admission_document": {*IMAGE_TYPES, "application/pdf"},
    "payment_proof": {*IMAGE_TYPES, "application/pdf"},
    "complaint_attachment": set(IMAGE_TYPES),
}
Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse "decompression bombs" (a tiny file that expands to gigabytes)


def unsupported(detail: str = "Only JPEG, PNG, WebP images and PDF documents are accepted") -> ProblemError:
    return ProblemError(415, "unsupported_file_type", "Unsupported file type", detail=detail)


def read_limited(stream: BinaryIO, limit: int) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ProblemError(
            413, "file_too_large", "The file is too large", detail=f"The limit is {limit // (1024 * 1024)} MB."
        )
    if not data:
        raise ProblemError(422, "validation_error", "The file is empty")
    return data


def sniff(data: bytes) -> str | None:
    """The real type from the content's first bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    return None


def reencode_image(data: bytes, mime: str) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if img.format != IMAGE_TYPES[mime]:
                raise unsupported("The image's content doesn't match its type")
            fmt = IMAGE_TYPES[mime]
            clean = img.convert("RGB") if fmt == "JPEG" and img.mode not in ("RGB", "L") else img.copy()
            out = io.BytesIO()
            # A fresh image object written without info/exif: metadata is not carried over.
            save_args: dict[str, object] = {"quality": 90} if fmt in ("JPEG", "WEBP") else {}
            clean.save(out, format=fmt, **save_args)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise unsupported("The image could not be read") from exc


def check_pdf(data: bytes) -> None:
    """Q66: a real PDF has the header at the start and the %%EOF marker at the end."""
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-1024:]:
        raise unsupported("This is not a complete PDF file")


def safe_name(name: str | None) -> str:
    """Keep the original name only for display; strip paths and control characters."""
    base = (name or "file").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in unicodedata.normalize("NFC", base) if ch.isprintable() and ch not in '"<>|*?:')
    return (base.strip(" .") or "file")[:200]


def file_aad(org_id: uuid.UUID, file_id: uuid.UUID, category: str) -> bytes:
    return f"{org_id}|{file_id}|{category}".encode()  # 03 §5.5.1


def store_upload(db: Session, p: Principal, category: str, stream: BinaryIO, filename: str | None) -> File:
    if category not in CATEGORY_TYPES:
        raise ProblemError(422, "validation_error", "Unknown category")
    data = read_limited(stream, PHOTO_MAX_BYTES if category == "student_photo" else MAX_BYTES)
    mime = sniff(data)
    if mime is None or mime not in CATEGORY_TYPES[category]:
        raise unsupported()
    if mime in IMAGE_TYPES:
        data = reencode_image(data, mime)
    else:
        check_pdf(data)
    file_id = uuid.uuid4()
    now = datetime.now(UTC)
    key = f"{p.org_id}/{category}/{now:%Y}/{now:%m}/{file_id}"
    sha = hashlib.sha256(data).digest()
    key_id = None
    payload = data
    if category == "identity_document":
        key_id, payload = crypto.encrypt(get_settings().data_keyring(), data, file_aad(p.org_id, file_id, category))
    row = File(
        id=file_id,
        org_id=p.org_id,
        category=category,
        storage_key=key,
        original_name=safe_name(filename),
        mime_type=mime,
        size_bytes=len(data),
        sha256=sha,
        uploaded_by=p.user_id,
        encryption_key_id=key_id,
    )
    db.add(row)
    db.flush()  # the database CHECKs (type, size, key format, encryption) run before any byte is written
    get_storage().save(key, payload)
    audit.record(db, p, "file.uploaded", "file", file_id, after={"category": category})
    return row


def read_content(f: File) -> bytes:
    """Bytes of the file, decrypted if needed; integrity checked against the stored hash."""
    data = get_storage().open(f.storage_key)
    if f.encryption_key_id:
        data = crypto.decrypt(
            get_settings().data_keyring(), f.encryption_key_id, data, file_aad(f.org_id, f.id, f.category)
        )
    if hashlib.sha256(data).digest() != f.sha256:
        raise ProblemError(500, "internal_error", "Something went wrong")
    return data


# --- signed links (04 §11.2) ------------------------------------------------------------------------


def _link_key() -> bytes:
    # Derived from the organization token key with a fixed label, so it can't be confused with a JWT.
    keys = get_settings().jwt_keys("hms-api")
    return hmac.new(keys.active_key(), b"hms-file-link-v1", hashlib.sha256).digest()


def _link_message(file_id: uuid.UUID, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, exp: int) -> bytes:
    return f"{file_id}|{org_id}|{user_id}|{session_id}|{exp}".encode()


@dataclass(frozen=True)
class SignedLink:
    url: str
    expires_at: datetime


def sign_link(f: File, p: Principal) -> SignedLink:
    if f.access_level == "restricted":
        raise ProblemError(404, "not_found", "Not found")  # 05 DOC-5: never a link for identity files
    exp = int(time.time()) + LINK_TTL_SECONDS
    sig = hmac.new(_link_key(), _link_message(f.id, p.org_id, p.user_id, p.session_id, exp), hashlib.sha256).hexdigest()
    url = f"/api/v1/files/{f.id}/content?o={p.org_id}&u={p.user_id}&s={p.session_id}&exp={exp}&sig={sig}"
    return SignedLink(url, datetime.fromtimestamp(exp, UTC))


def verify_link(
    file_id: uuid.UUID, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, exp: int, sig: str
) -> None:
    expected = hmac.new(_link_key(), _link_message(file_id, org_id, user_id, session_id, exp), hashlib.sha256)
    if exp < time.time() or not hmac.compare_digest(expected.hexdigest(), sig):
        raise ProblemError(404, "not_found", "Not found")


def content_headers(f: File, *, inline: bool) -> dict[str, str]:
    disposition = "inline" if inline and f.mime_type.startswith("image/") else "attachment"
    return {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(f.original_name)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "private, no-store",
    }


def unattached_clause() -> ColumnElement[bool]:
    """SQL condition: a file no record references (purged after 24 h, visible to its uploader only)."""
    from app.modules.students.models import Student, StudentDocument

    return ~or_(
        exists().where(Student.photo_file_id == File.id),
        exists().where(StudentDocument.file_id == File.id),
        # Their modules come in Phases 5 and 8; the tables already exist.
        text("EXISTS (SELECT 1 FROM payment_submissions ps WHERE ps.proof_file_id = files.id)"),
        text("EXISTS (SELECT 1 FROM complaint_attachments ca WHERE ca.file_id = files.id)"),
    )


def purge_unattached(db: Session) -> int:
    """Worker, per organization: soft-delete and remove files nobody attached within 24 hours."""
    rows = db.scalars(
        select(File).where(
            File.deleted_at.is_(None), File.created_at < text("app_now() - interval '24 hours'"), unattached_clause()
        )
    ).all()
    for f in rows:
        get_storage().delete(f.storage_key)
        f.deleted_at = f.purged_at = datetime.now(UTC)
    db.flush()
    return len(rows)
