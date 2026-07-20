"""Document AST data models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class TextRun:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool | None = None
    font_name: str | None = None
    font_size_pt: float | None = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "font_name": self.font_name,
            "font_size_pt": self.font_size_pt,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TextRun":
        return cls(
            text=d.get("text", ""),
            bold=d.get("bold", False),
            italic=d.get("italic", False),
            underline=d.get("underline"),
            font_name=d.get("font_name"),
            font_size_pt=d.get("font_size_pt"),
        )


@dataclass
class HeadingElement:
    kind: Literal["heading"] = "heading"
    element_id: str = ""
    block_index: int = 0
    level: int = 1
    text: str = ""
    order: int = 0
    runs: list[TextRun] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "element_id": self.element_id,
            "block_index": self.block_index,
            "level": self.level,
            "text": self.text,
            "order": self.order,
            "runs": [r.to_dict() for r in self.runs],
        }


@dataclass
class ParagraphElement:
    kind: Literal["paragraph"] = "paragraph"
    element_id: str = ""
    block_index: int = 0
    text: str = ""
    order: int = 0
    runs: list[TextRun] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "element_id": self.element_id,
            "block_index": self.block_index,
            "text": self.text,
            "order": self.order,
            "runs": [r.to_dict() for r in self.runs],
        }


@dataclass
class TableCellElement:
    kind: Literal["table_cell"] = "table_cell"
    element_id: str = ""
    table_index: int = 0
    row_index: int = 0
    col_index: int = 0
    text: str = ""
    order: int = 0
    runs: list[TextRun] = field(default_factory=list)
    content_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "element_id": self.element_id,
            "table_index": self.table_index,
            "row_index": self.row_index,
            "col_index": self.col_index,
            "text": self.text,
            "order": self.order,
            "runs": [r.to_dict() for r in self.runs],
            "content_hash": self.content_hash,
        }


DocumentElement = HeadingElement | ParagraphElement | TableCellElement


@dataclass
class DocumentAST:
    file_id: str
    filename: str
    elements: list[DocumentElement] = field(default_factory=list)
    total_elements: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "elements": [e.to_dict() for e in self.elements],
            "total_elements": self.total_elements,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentAST":
        elements = []
        for e in d.get("elements", []):
            kind = e.get("kind")
            if kind == "heading":
                runs = [TextRun.from_dict(r) for r in e.get("runs", [])]
                elements.append(HeadingElement(**{**e, "runs": runs}))
            elif kind == "table_cell":
                runs = [TextRun.from_dict(r) for r in e.get("runs", [])]
                elements.append(TableCellElement(**{**e, "runs": runs}))
            elif kind == "paragraph":
                runs = [TextRun.from_dict(r) for r in e.get("runs", [])]
                elements.append(ParagraphElement(**{**e, "runs": runs}))
        return cls(
            file_id=d["file_id"],
            filename=d["filename"],
            elements=elements,
            total_elements=d.get("total_elements", len(elements)),
        )


def compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]
