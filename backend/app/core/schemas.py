"""Shared request/response building blocks for the admin API (docs/04 §1, §8, §10)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    """Request bodies: unknown fields are a 422 (no mass assignment, 04 §1)."""

    model_config = ConfigDict(extra="forbid")


class Versioned(Strict):
    """PATCH bodies carry back the version the client read (Q70)."""

    version: int = Field(ge=1)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageInfo(BaseModel):
    number: int
    size: int
    total_items: int
    total_pages: int


class Page[T](BaseModel):
    items: list[T]
    page: PageInfo


PageNumber = Annotated[int, Query(ge=1, le=100_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
