"""S3-compatible storage: Signature V4 matches AWS's published example, and files uploaded through
the API land in (and come back from) the bucket, signed and under the organization's prefix."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx
import pytest

from novara.config import Settings
from novara.main import create_app
from novara.modules.files.storage import ObjectNotFound, S3ObjectStorage, StorageError, sign_v4

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_signature_matches_the_aws_example():
    # "Example: GET Object" from the AWS S3 Signature Version 4 documentation.
    auth = sign_v4(method="GET", host="examplebucket.s3.amazonaws.com", path="/test.txt", query="",
                   headers={"range": "bytes=0-9", "x-amz-content-sha256": EMPTY_SHA256,
                            "x-amz-date": "20130524T000000Z"},
                   payload_sha256=EMPTY_SHA256, access_key="AKIAIOSFODNN7EXAMPLE",
                   secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1",
                   amz_date="20130524T000000Z")
    assert auth == ("AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request,"
                    "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date,"
                    "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


class FakeBucket:
    """An S3 endpoint in memory that checks what a real one would: the signature's shape and the body hash."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(503)
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
        assert request.headers["x-amz-content-sha256"] == hashlib.sha256(request.content).hexdigest()
        path = request.url.path
        if request.method == "PUT":
            self.objects[path] = request.content
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, content=self.objects[path]) if path in self.objects else httpx.Response(404)
        if request.method == "DELETE":
            self.objects.pop(path, None)
            return httpx.Response(204)
        return httpx.Response(405)


def storage(bucket: FakeBucket, **kw) -> S3ObjectStorage:
    return S3ObjectStorage(endpoint="https://s3.example.test", bucket="novara-files", region="me-central-1",
                           access_key="AKID", secret_key="secret", client=httpx.Client(transport=httpx.MockTransport(bucket)),
                           clock=lambda: datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc), **kw)


def test_put_get_delete_and_errors():
    bucket = FakeBucket()
    s = storage(bucket, prefix="prod")
    s.put("orgs/o1/files/f1", "مرحبا".encode(), "text/plain")
    assert list(bucket.objects) == ["/novara-files/prod/orgs/o1/files/f1"]
    assert bucket.requests[0].headers["authorization"].split(",")[0].endswith("/20261023/me-central-1/s3/aws4_request")
    assert s.get("orgs/o1/files/f1").decode() == "مرحبا"
    s.delete("orgs/o1/files/f1")
    with pytest.raises(ObjectNotFound):
        s.get("orgs/o1/files/f1")
    with pytest.raises(ValueError):
        s.get("orgs/o1/../o2/files/f1")
    bucket.fail = True
    with pytest.raises(StorageError):
        s.put("orgs/o1/files/f2", b"x", "text/plain")


def test_files_api_on_s3(tmp_path):
    bucket = FakeBucket()
    settings = Settings(env="test", database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
                        jwt_secret="test-secret-that-is-long-enough-1234567890", max_upload_bytes=1024)
    app = create_app(settings, storage=storage(bucket), create_schema=True)
    from fastapi.testclient import TestClient

    from tests.conftest import Api

    api = Api(TestClient(app))
    api.signup("s3@example.com")
    org = api.create_org("s3@example.com", "s3-co")
    up = api.req("POST", "s3@example.com", f"/orgs/{org}/files", params={"filename": "report.txt"}, content=b"quarterly numbers",
                 headers={"Content-Type": "text/plain"})
    assert up.status_code == 201, up.text
    assert list(bucket.objects) == [f"/novara-files/orgs/{org}/files/{up.json()['id']}"]
    assert api.req("GET", "s3@example.com", f"/orgs/{org}/files/{up.json()['id']}/content").content == b"quarterly numbers"


def test_s3_settings_are_checked():
    with pytest.raises(RuntimeError, match="NOVARA_S3_BUCKET"):
        Settings(jwt_secret="x" * 40, storage_backend="s3", s3_endpoint="https://s3.example.test",
                 s3_access_key_id="a", s3_secret_access_key="b").validate()
    with pytest.raises(RuntimeError, match="https"):
        Settings(env="production", jwt_secret="x" * 40, encryption_key="y" * 40, storage_backend="s3",
                 s3_endpoint="http://minio:9000", s3_bucket="b", s3_access_key_id="a", s3_secret_access_key="b").validate()
