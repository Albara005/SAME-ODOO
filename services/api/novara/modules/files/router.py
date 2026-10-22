from __future__ import annotations

import hashlib
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from novara.db import new_id
from novara.deps import OrgContext, client_ip, get_db, require
from novara.modules.access import permissions as P
from novara.modules.audit import service as audit
from novara.modules.files.models import StoredFile
from novara.modules.files.storage import object_key

router = APIRouter(tags=["files"])


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_by: str | None
    created_at: datetime


@router.post("/orgs/{org_id}/files", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    filename: str = Query(min_length=1, max_length=255),
    ctx: OrgContext = Depends(require(P.FILES_WRITE)),
    db: Session = Depends(get_db),
):
    """Upload the raw request body as a file. Send the real Content-Type header."""
    limit = request.app.state.settings.max_upload_bytes
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty file")
    file_id = new_id()
    key = object_key(ctx.organization.id, file_id)
    content_type = request.headers.get("content-type", "application/octet-stream")[:127]
    storage = request.app.state.storage
    storage.put(key, bytes(data), content_type)
    row = StoredFile(id=file_id, storage_key=key, filename=filename, content_type=content_type,
                     size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), created_by=ctx.user.id)
    db.add(row)
    audit.record(db, actor_user_id=ctx.user.id, action="file.uploaded", target_type="file", target_id=file_id,
                 details={"filename": filename, "size_bytes": len(data)}, ip_address=client_ip(request))
    try:
        db.commit()
    except Exception:
        storage.delete(key)
        raise
    return row


@router.get("/orgs/{org_id}/files", response_model=list[FileOut])
def list_files(_: OrgContext = Depends(require(P.FILES_READ)), db: Session = Depends(get_db)):
    return db.scalars(select(StoredFile).order_by(StoredFile.created_at.desc())).all()


def _get_file(db: Session, file_id: str) -> StoredFile:
    row = db.get(StoredFile, file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    return row


@router.get("/orgs/{org_id}/files/{file_id}/content")
def download_file(file_id: str, request: Request, _: OrgContext = Depends(require(P.FILES_READ)),
                  db: Session = Depends(get_db)) -> Response:
    row = _get_file(db, file_id)
    data = request.app.state.storage.get(row.storage_key)
    return Response(
        content=data,
        media_type=row.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/orgs/{org_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_file(file_id: str, request: Request, ctx: OrgContext = Depends(require(P.FILES_DELETE)),
                db: Session = Depends(get_db)):
    row = _get_file(db, file_id)
    audit.record(db, actor_user_id=ctx.user.id, action="file.deleted", target_type="file", target_id=row.id,
                 details={"filename": row.filename}, ip_address=client_ip(request))
    key = row.storage_key
    db.delete(row)
    db.commit()
    request.app.state.storage.delete(key)
