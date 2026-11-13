"""File metadata (docs/03 §5.5). The bytes live in app/core/storage.py."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Computed, LargeBinary, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.orm import created_at, tz


class File(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # chosen by the app: it is part of the
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # encryption's associated data, so it's needed first
    category: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)  # of the PLAINTEXT
    uploaded_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_at()
    deleted_at: Mapped[datetime | None] = tz()
    purged_at: Mapped[datetime | None] = tz()
    access_level: Mapped[str] = mapped_column(
        Text,
        Computed("CASE WHEN category = 'identity_document' THEN 'restricted' ELSE 'standard' END"),
        nullable=True,  # a generated column carries no NOT NULL in the database; it is always set
    )
    encryption_key_id: Mapped[str | None] = mapped_column(Text)
