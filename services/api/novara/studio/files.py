"""Project file paths, limits and content-addressed blob storage."""

from __future__ import annotations

import hashlib
import posixpath
import re

from novara.modules.files.storage import ObjectStorage

MAX_FILES = 200
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_DEPTH = 6

_SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}$")


class FileError(ValueError):
    pass


def clean_path(path: str) -> str:
    """Normalise a project-relative path and refuse anything that could escape the project."""
    if not path or "\\" in path or path.startswith("/"):
        raise FileError(f"invalid path: {path!r}")
    norm = posixpath.normpath(path)
    parts = norm.split("/")
    if norm != path or len(parts) > MAX_DEPTH or any(not _SEGMENT.match(p) for p in parts):
        raise FileError(f"invalid path: {path!r} (letters, digits, '.', '_', '-'; no hidden files or '..')")
    return norm


def blob_key(organization_id: str, sha256: str) -> str:
    return f"orgs/{organization_id}/studio/blobs/{sha256[:2]}/{sha256}"


def put_blob(storage: ObjectStorage, organization_id: str, data: bytes) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise FileError(f"file is larger than {MAX_FILE_BYTES // 1024} KB")
    sha = hashlib.sha256(data).hexdigest()
    storage.put(blob_key(organization_id, sha), data, "application/octet-stream")
    return sha


def get_blob(storage: ObjectStorage, organization_id: str, sha256: str) -> bytes:
    return storage.get(blob_key(organization_id, sha256))


def check_limits(files: dict[str, str], sizes: dict[str, int]) -> None:
    if len(files) > MAX_FILES:
        raise FileError(f"a project can have at most {MAX_FILES} files")
    if sum(sizes.values()) > MAX_TOTAL_BYTES:
        raise FileError(f"a project can be at most {MAX_TOTAL_BYTES // (1024 * 1024)} MB")
