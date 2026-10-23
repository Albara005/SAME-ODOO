"""Object storage abstraction.

Production uses an S3-compatible bucket (AWS S3, Cloudflare R2, MinIO, Wasabi...); the local
filesystem backend keeps development and tests self-contained. Keys are always prefixed with
the organization id so one organization's objects never share a path with another's.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote, urlsplit

import httpx


class StorageError(RuntimeError):
    """The storage service failed or refused the request."""


class ObjectNotFound(FileNotFoundError):
    pass


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalObjectStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("storage key escapes the storage root")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


# ---------------------------------------------------------------- S3-compatible storage


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign_v4(*, method: str, host: str, path: str, query: str, headers: dict[str, str], payload_sha256: str,
            access_key: str, secret_key: str, region: str, amz_date: str, service: str = "s3") -> str:
    """The Authorization header for AWS Signature Version 4. ``headers`` are the ones to sign
    (host is added); ``path`` must already be URI-encoded."""
    signed = {k.lower(): " ".join(v.strip().split()) for k, v in headers.items()} | {"host": host}
    names = sorted(signed)
    canonical = "\n".join([method, path, query, "".join(f"{n}:{signed[n]}\n" for n in names), ";".join(names),
                           payload_sha256])
    day = amz_date[:8]
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope},SignedHeaders={';'.join(names)},Signature={signature}"


class S3ObjectStorage:
    """A bucket on any S3-compatible service, addressed path-style (``{endpoint}/{bucket}/{key}``),
    which every provider supports. Requests are signed with Signature V4 and the payload hash, so
    the service rejects anything altered in transit."""

    def __init__(self, *, endpoint: str, bucket: str, region: str, access_key: str, secret_key: str,
                 prefix: str = "", client: httpx.Client | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        parts = urlsplit(endpoint.rstrip("/"))
        if parts.scheme not in ("https", "http") or not parts.netloc:
            raise ValueError("the S3 endpoint must be a URL such as https://s3.eu-central-1.amazonaws.com")
        self.base, self.host = f"{parts.scheme}://{parts.netloc}", parts.netloc
        self.bucket, self.region, self.prefix = bucket, region, prefix.strip("/")
        self.access_key, self.secret_key = access_key, secret_key
        self.client = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0))
        self.clock = clock

    def _path(self, key: str) -> str:
        full = f"{self.prefix}/{key}" if self.prefix else key
        if ".." in full.split("/"):
            raise ValueError("storage key escapes the bucket prefix")
        return "/" + quote(self.bucket, safe="") + "/" + quote(full, safe="/-_.~")

    def _send(self, method: str, key: str, body: bytes = b"", content_type: str | None = None) -> httpx.Response:
        path = self._path(key)
        digest = hashlib.sha256(body).hexdigest()
        amz_date = self.clock().strftime("%Y%m%dT%H%M%SZ")
        headers = {"x-amz-content-sha256": digest, "x-amz-date": amz_date}
        if content_type:
            headers["content-type"] = content_type
        headers["Authorization"] = sign_v4(method=method, host=self.host, path=path, query="", headers=headers,
                                           payload_sha256=digest, access_key=self.access_key,
                                           secret_key=self.secret_key, region=self.region, amz_date=amz_date)
        try:
            return self.client.request(method, self.base + path, content=body, headers=headers)
        except httpx.HTTPError as e:
            raise StorageError(f"object storage unreachable: {type(e).__name__}") from e

    def put(self, key: str, data: bytes, content_type: str) -> None:
        r = self._send("PUT", key, data, content_type)
        if r.status_code != 200:
            raise StorageError(f"object storage refused the upload (HTTP {r.status_code})")

    def get(self, key: str) -> bytes:
        r = self._send("GET", key)
        if r.status_code == 404:
            raise ObjectNotFound(key)
        if r.status_code != 200:
            raise StorageError(f"object storage refused the download (HTTP {r.status_code})")
        return r.content

    def delete(self, key: str) -> None:
        r = self._send("DELETE", key)
        if r.status_code not in (200, 204, 404):
            raise StorageError(f"object storage refused the delete (HTTP {r.status_code})")


def storage_from_settings(settings) -> ObjectStorage:
    if settings.storage_backend == "s3":
        return S3ObjectStorage(endpoint=settings.s3_endpoint, bucket=settings.s3_bucket, region=settings.s3_region,
                               access_key=settings.s3_access_key_id, secret_key=settings.s3_secret_access_key,
                               prefix=settings.s3_prefix)
    return LocalObjectStorage(settings.storage_root)


def object_key(organization_id: str, file_id: str) -> str:
    return f"orgs/{organization_id}/files/{file_id}"
