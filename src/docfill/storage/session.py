"""In-memory session registry — tracks uploaded documents per session."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class DocumentRecord:
    file_id: str
    filename: str
    file_path: Path
    ast_path: Path | None = None
    version: int = 1
    total_elements: int = 0
    content_hash: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    modified_at: datetime = field(default_factory=datetime.utcnow)


class SessionRegistry:
    """Thread-unsafe in-process document registry, good for single-user demo."""

    def __init__(self) -> None:
        self._docs: dict[str, DocumentRecord] = {}
        self._current_id: str | None = None

    def register(self, record: DocumentRecord) -> None:
        self._docs[record.file_id] = record
        self._current_id = record.file_id

    def get(self, file_id: str) -> DocumentRecord | None:
        return self._docs.get(file_id)

    def update(self, file_id: str, **kwargs) -> None:
        rec = self._docs.get(file_id)
        if rec:
            for k, v in kwargs.items():
                setattr(rec, k, v)
            rec.modified_at = datetime.utcnow()

    def list_all(self) -> list[DocumentRecord]:
        return list(self._docs.values())

    @property
    def current_id(self) -> str | None:
        return self._current_id

    def increment_version(self, file_id: str) -> None:
        rec = self._docs.get(file_id)
        if rec:
            rec.version += 1
