"""Local filesystem storage backend — drop-in replacement for S3 in dev/demo."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from docscribe.ast.models import DocumentAST


class LocalDocumentStore:
    """Stores DOCX files and their AST JSON on the local filesystem.

    Directory layout::

        root/
          <file_id>/
            input/  <file>.docx
            input/  <file>.ast.json
            output/ <file>.docx   (after edits)
            output/ <file>.ast.json
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, file_id: str, is_output: bool = False) -> Path:
        folder = "output" if is_output else "input"
        d = self.root / file_id / folder
        d.mkdir(parents=True, exist_ok=True)
        return d

    def store_file(self, file_id: str, src_path: str | Path, is_output: bool = False) -> Path:
        src = Path(src_path)
        dest = self._dir(file_id, is_output) / src.name
        shutil.copy2(src, dest)
        return dest

    def store_ast(self, file_id: str, ast: DocumentAST, is_output: bool = False) -> Path:
        dest = self._dir(file_id, is_output) / f"{file_id}.ast.json"
        dest.write_text(json.dumps(ast.to_dict(), default=str), encoding="utf-8")
        return dest

    def get_file(self, file_id: str, is_output: bool = False) -> Path | None:
        folder = self._dir(file_id, is_output)
        docx_files = list(folder.glob("*.docx"))
        return docx_files[0] if docx_files else None

    def get_ast(self, file_id: str, is_output: bool = False) -> DocumentAST | None:
        ast_path = self._dir(file_id, is_output) / f"{file_id}.ast.json"
        if not ast_path.exists():
            # Try input if output not found
            if is_output:
                return self.get_ast(file_id, is_output=False)
            return None
        data = json.loads(ast_path.read_text(encoding="utf-8"))
        return DocumentAST.from_dict(data)

    def list_files(self) -> list[str]:
        return [d.name for d in self.root.iterdir() if d.is_dir()]
