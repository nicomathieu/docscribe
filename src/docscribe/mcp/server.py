"""FastMCP server exposing document-fill tools.

Run with:
    uvicorn docscribe.mcp.server:app --port 8000

Environment variables:
    STORE_BACKEND   "local" (default) or "s3"
    STORE_DIR       Local store path (default: ./docscribe_store)  [local only]
    S3_BUCKET       S3 bucket name                                  [s3 only]
    S3_FOLDER       S3 key prefix (default: docscribe)              [s3 only]
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastmcp import FastMCP

from docscribe.ast.converter import apply_cell_edit, apply_heading_edit, build_ast
from docscribe.ast.models import DocumentAST, TableCellElement, HeadingElement, TextRun
from docscribe.ast.run_formatter import add_top_padding, create_runs_from_template
from docscribe.storage.local import LocalDocumentStore
from docscribe.storage.session import DocumentRecord, SessionRegistry

_backend = os.getenv("STORE_BACKEND", "local").lower()
if _backend == "s3":
    from docscribe.storage.s3 import S3DocumentStore
    store = S3DocumentStore()
else:
    store = LocalDocumentStore(Path(os.getenv("STORE_DIR", "./docscribe_store")))

session = SessionRegistry()

mcp = FastMCP(name="docscribe")
app = mcp.http_app()


# ---------------------------------------------------------------------------
# Tool 1 — upload_document
# ---------------------------------------------------------------------------

@mcp.tool
def upload_document(file_path: str, file_id: str | None = None) -> dict:
    """Upload a DOCX file and generate its AST.

    Args:
        file_path: Absolute or relative path to the .docx file.
        file_id:   Optional identifier (defaults to the stem of the filename).

    Returns:
        dict with status, file_id, total_elements.
    """
    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "error_code": "FILE_NOT_FOUND", "message": str(path)}

    fid = file_id or path.stem.lower().replace(" ", "-").replace("_", "-")

    existing = session.get(fid)
    if existing:
        return {"status": "success", "file_id": fid, "skipped": True,
                "total_elements": existing.total_elements, "version": existing.version}

    ast = build_ast(path)
    ast.file_id = fid

    stored_file = store.store_file(fid, path, is_output=False)
    stored_ast = store.store_ast(fid, ast, is_output=False)

    session.register(
        DocumentRecord(
            file_id=fid,
            filename=path.name,
            file_path=stored_file,
            ast_path=stored_ast,
            total_elements=ast.total_elements,
        )
    )

    return {"status": "success", "file_id": fid, "total_elements": ast.total_elements, "version": 1}


# ---------------------------------------------------------------------------
# Tool 2 — get_session_documents
# ---------------------------------------------------------------------------

@mcp.tool
def get_session_documents(include_metadata: bool = True) -> dict:
    """List all documents registered in the current session.

    Returns:
        dict with documents list and total_count.
    """
    docs = session.list_all()
    result = []
    for doc in docs:
        entry: dict = {"file_id": doc.file_id, "filename": doc.filename, "version": doc.version}
        if include_metadata:
            entry.update({
                "total_elements": doc.total_elements,
                "content_hash": doc.content_hash,
                "is_current": doc.file_id == session.current_id,
                "created_at": doc.created_at.isoformat(),
                "modified_at": doc.modified_at.isoformat(),
            })
        result.append(entry)
    return {"documents": result, "total_count": len(result)}


# ---------------------------------------------------------------------------
# Tool 3 — load_document_ast
# ---------------------------------------------------------------------------

@mcp.tool
def load_document_ast(file_id: str) -> dict:
    """Return the full AST of a previously uploaded document.

    Args:
        file_id: Identifier from upload_document.

    Returns:
        dict with file_id, total_elements, elements (list of element dicts).
    """
    rec = session.get(file_id)
    if not rec:
        return {"status": "error", "error_code": "FILE_NOT_FOUND",
                "message": f"{file_id} not found. Call upload_document first."}

    # Prefer output AST (post-edit), fall back to input
    ast = store.get_ast(file_id, is_output=True) or store.get_ast(file_id, is_output=False)
    if not ast:
        return {"status": "error", "error_code": "AST_NOT_FOUND", "message": f"AST for {file_id} not found."}

    elements = []
    for el in ast.elements:
        d = el.to_dict()
        # Convert runs to plain dicts for JSON serialisation
        d["runs"] = [r.to_dict() for r in el.runs] if el.runs else []
        elements.append(d)

    return {
        "status": "success",
        "file_id": file_id,
        "filename": rec.filename,
        "total_elements": ast.total_elements,
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Tool 4 — edit_document
# ---------------------------------------------------------------------------

@mcp.tool
def edit_document(file_id: str, edits: list[dict]) -> dict:
    """Apply AST-based edits to a document and save the result.

    Each edit dict must have:
        - ``type``:       ``"table_cell"`` or ``"heading"``
        - ``element_id``: exact element_id from load_document_ast
        - ``changes``:    ``{"text": "new value"}``

    Bold labels from the template are preserved automatically.

    Returns:
        dict with status, edits_applied, edits_failed, results.
    """
    rec = session.get(file_id)
    if not rec:
        return {"status": "error", "error_code": "FILE_NOT_FOUND",
                "message": f"{file_id} not found. Call upload_document first."}

    # Load current AST
    ast = store.get_ast(file_id, is_output=True) or store.get_ast(file_id, is_output=False)
    if not ast:
        return {"status": "error", "error_code": "AST_NOT_FOUND"}

    # Resolve current DOCX path (output overrides input)
    docx_path = store.get_file(file_id, is_output=True) or store.get_file(file_id, is_output=False)
    if not docx_path:
        return {"status": "error", "error_code": "FILE_NOT_FOUND", "message": "DOCX not found on disk."}

    # Work on a temp copy so a crash doesn't corrupt the store
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    shutil.copy2(docx_path, tmp_path)

    element_map = {el.element_id: el for el in ast.elements}

    results = []
    applied = 0
    failed = 0

    for edit in edits:
        edit_type = edit.get("type")
        eid = edit.get("element_id")
        changes = edit.get("changes", {})

        if not eid or eid not in element_map:
            results.append({"element_id": eid, "status": "failed", "message": "element_id not found"})
            failed += 1
            continue

        original = element_map[eid]

        try:
            if edit_type == "table_cell":
                new_text = changes.get("text", original.text)
                template_runs = getattr(original, "runs", []) or []
                orig_text = getattr(original, "text", "") or ""

                font_name = (template_runs[0].font_name or "Calibri") if template_runs else "Calibri"
                font_size = (template_runs[0].font_size_pt or 12.0) if template_runs else 12.0

                runs = create_runs_from_template(
                    new_text,
                    template_runs=template_runs,
                    font_name=font_name,
                    font_size_pt=font_size,
                    original_text=orig_text,
                )
                runs, new_text = add_top_padding(runs, new_text, font_name, font_size)

                cell = TableCellElement(
                    element_id=eid,
                    table_index=original.table_index,
                    row_index=original.row_index,
                    col_index=original.col_index,
                    text=new_text,
                    runs=runs,
                    content_hash=original.content_hash,
                )
                msg = apply_cell_edit(tmp_path, cell)
                results.append({"element_id": eid, "status": "applied", "message": msg})
                applied += 1

            elif edit_type == "heading":
                updated = HeadingElement(
                    element_id=eid,
                    block_index=original.block_index,
                    level=changes.get("level", original.level),
                    text=changes.get("text", original.text),
                    order=original.order,
                )
                msg = apply_heading_edit(tmp_path, updated)
                results.append({"element_id": eid, "status": "applied", "message": msg})
                applied += 1

            else:
                results.append({"element_id": eid, "status": "failed", "message": f"Unknown type: {edit_type}"})
                failed += 1

        except Exception as exc:
            results.append({"element_id": eid, "status": "failed", "message": str(exc)})
            failed += 1

    if applied == 0:
        tmp_path.unlink(missing_ok=True)
        return {"status": "error", "error_code": "ALL_EDITS_FAILED",
                "edits_applied": 0, "edits_failed": failed, "results": results}

    # Re-generate AST from edited file
    updated_ast = build_ast(tmp_path)
    updated_ast.file_id = file_id

    # Persist output
    stored_file = store.store_file(file_id, tmp_path, is_output=True)
    stored_ast_path = store.store_ast(file_id, updated_ast, is_output=True)
    tmp_path.unlink(missing_ok=True)

    session.update(file_id, file_path=stored_file, ast_path=stored_ast_path,
                   total_elements=updated_ast.total_elements)

    return {
        "status": "success",
        "file_id": file_id,
        "edits_applied": applied,
        "edits_failed": failed,
        "results": results,
        "partial_success": failed > 0,
    }


# ---------------------------------------------------------------------------
# Tool 5 — validate_document_state
# ---------------------------------------------------------------------------

@mcp.tool
def validate_document_state(file_id: str, create_new_version: bool = False) -> dict:
    """Validate document integrity and optionally create a new version.

    Re-generates the AST from the stored DOCX and checks that element counts
    match the stored AST. If *create_new_version* is True and validation passes,
    the version counter is incremented.

    Returns:
        dict with validation results and optional save status.
    """
    rec = session.get(file_id)
    if not rec:
        return {"status": "error", "error_code": "FILE_NOT_FOUND"}

    docx_path = store.get_file(file_id, is_output=True) or store.get_file(file_id, is_output=False)
    stored_ast = store.get_ast(file_id, is_output=True) or store.get_ast(file_id, is_output=False)

    if not docx_path or not stored_ast:
        return {"status": "error", "error_code": "MISSING_DATA"}

    fresh_ast = build_ast(docx_path)
    passed = (
        fresh_ast.total_elements == stored_ast.total_elements
        and len(fresh_ast.elements) == len(stored_ast.elements)
    )

    result: dict = {
        "status": "success",
        "file_id": file_id,
        "validation": {
            "passed": passed,
            "stored_elements": stored_ast.total_elements,
            "regenerated_elements": fresh_ast.total_elements,
        },
    }

    if passed and create_new_version:
        session.increment_version(file_id)
        updated = session.get(file_id)
        result["save"] = {"status": "success", "version": updated.version if updated else None}
    elif create_new_version:
        result["save"] = {"status": "skipped", "reason": "Validation failed"}

    return result
