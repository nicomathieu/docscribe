"""S3 storage backend — same interface as LocalDocumentStore."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from docscribe.ast.models import DocumentAST


class S3DocumentStore:
    """Stores DOCX files and AST JSON in an S3 bucket.

    S3 key layout::

        {folder}/{file_id}/input/<file>.docx
        {folder}/{file_id}/input/<file>.ast.json
        {folder}/{file_id}/output/<file>.docx
        {folder}/{file_id}/output/<file>.ast.json

    Environment variables (fallbacks if not passed to constructor):
        S3_BUCKET   — bucket name (required)
        S3_FOLDER   — key prefix (default: docscribe)
    """

    def __init__(
        self,
        bucket: str | None = None,
        folder: str | None = None,
        s3_client=None,
    ) -> None:
        try:
            import boto3
        except ImportError as e:
            raise ImportError(
                "boto3 is required for S3 storage. Install with: pip install boto3"
            ) from e

        self.bucket = bucket or os.getenv("S3_BUCKET")
        if not self.bucket:
            raise ValueError("S3_BUCKET must be set (env var or constructor param)")
        self.folder = (folder or os.getenv("S3_FOLDER", "docscribe")).rstrip("/")
        if s3_client:
            self._s3 = s3_client
        else:
            profile = os.getenv("AWS_PROFILE")
            session = boto3.Session(profile_name=profile) if profile else boto3.Session()
            self._s3 = session.client("s3")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, file_id: str, filename: str, is_output: bool) -> str:
        slot = "output" if is_output else "input"
        return f"{self.folder}/{file_id}/{slot}/{filename}"

    def _put(self, key: str, body: bytes) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=body)

    def _get_bytes(self, key: str) -> bytes | None:
        try:
            resp = self._s3.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except self._s3.exceptions.NoSuchKey:
            return None
        except Exception:
            return None

    def _list_keys(self, prefix: str) -> list[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    # ------------------------------------------------------------------
    # Public interface — mirrors LocalDocumentStore exactly
    # ------------------------------------------------------------------

    def store_file(self, file_id: str, src_path: str | Path, is_output: bool = False) -> Path:
        src = Path(src_path)
        key = self._key(file_id, src.name, is_output)
        self._put(key, src.read_bytes())
        # Return a Path-like sentinel so callers (session registry) have something to store
        return Path(f"s3://{self.bucket}/{key}")

    def store_ast(self, file_id: str, ast: DocumentAST, is_output: bool = False) -> Path:
        key = self._key(file_id, f"{file_id}.ast.json", is_output)
        self._put(key, json.dumps(ast.to_dict(), default=str).encode())
        return Path(f"s3://{self.bucket}/{key}")

    def get_file(self, file_id: str, is_output: bool = False) -> Path | None:
        slot = "output" if is_output else "input"
        prefix = f"{self.folder}/{file_id}/{slot}/"
        keys = [k for k in self._list_keys(prefix) if k.endswith(".docx")]
        if not keys:
            return None
        data = self._get_bytes(keys[0])
        if data is None:
            return None
        # Download to a named temp file so python-docx can open it
        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)

    def get_ast(self, file_id: str, is_output: bool = False) -> DocumentAST | None:
        key = self._key(file_id, f"{file_id}.ast.json", is_output)
        data = self._get_bytes(key)
        if data is None:
            if is_output:
                return self.get_ast(file_id, is_output=False)
            return None
        return DocumentAST.from_dict(json.loads(data.decode()))

    def list_files(self) -> list[str]:
        prefix = f"{self.folder}/"
        keys = self._list_keys(prefix)
        # Extract unique file_ids (second path component)
        ids: set[str] = set()
        for k in keys:
            parts = k[len(prefix):].split("/")
            if parts:
                ids.add(parts[0])
        return sorted(ids)
