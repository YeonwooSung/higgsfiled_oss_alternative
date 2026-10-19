"""Object storage. Browsers only get short-lived signed URLs for one object.

LocalStorage serves its signed URLs through the API (`/api/v1/storage/o/{token}`);
S3Storage (MinIO locally) hands out presigned S3 URLs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4",
            "application/json": ".json", "application/zip": ".zip"}


def object_key(session_id, project_id, asset_id, role: str, mime: str) -> str:
    return f"{session_id}/{project_id}/{asset_id}/{role}{MIME_EXT.get(mime, '.bin')}"


class Storage(Protocol):
    def upload_url(self, key: str, ttl: int, mime: str, max_bytes: int) -> tuple[str, dict, datetime]: ...
    def read_url(self, key: str, ttl: int, mime: str | None = None, filename: str | None = None) -> str: ...
    def put_bytes(self, key: str, data: bytes, mime: str | None = None) -> None: ...
    def put_file(self, key: str, path: Path, mime: str | None = None) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def get_file(self, key: str, path: Path) -> Path: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def delete_prefix(self, prefix: str) -> None: ...


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class TokenError(Exception):
    pass


class LocalStorage:
    URL_PREFIX = "/api/v1/storage/o/"

    def __init__(self, root: Path, secret: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._secret = secret.encode()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid object key")
        return p

    # -- signed tokens ------------------------------------------------------
    def sign(self, claims: dict) -> str:
        body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        sig = _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def verify(self, token: str, op: str) -> dict:
        try:
            body, sig = token.split(".", 1)
            expected = _b64(hmac.new(self._secret, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, expected):
                raise TokenError("bad signature")
            claims = json.loads(_unb64(body))
        except (ValueError, json.JSONDecodeError) as e:
            raise TokenError("malformed token") from e
        if claims.get("op") != op or claims.get("exp", 0) < time.time():
            raise TokenError("expired or wrong operation")
        return claims

    def upload_url(self, key: str, ttl: int, mime: str, max_bytes: int) -> tuple[str, dict, datetime]:
        exp = int(time.time()) + ttl
        token = self.sign({"op": "put", "k": key, "exp": exp, "mime": mime, "max": max_bytes})
        return self.URL_PREFIX + token, {"Content-Type": mime}, datetime.fromtimestamp(exp, UTC)

    def read_url(self, key: str, ttl: int, mime: str | None = None, filename: str | None = None) -> str:
        claims = {"op": "get", "k": key, "exp": int(time.time()) + ttl, "mime": mime}
        if filename:
            claims["fn"] = filename
        return self.URL_PREFIX + self.sign(claims)

    # -- objects -----------------------------------------------------------
    def put_bytes(self, key: str, data: bytes, mime: str | None = None) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)

    def put_file(self, key: str, path: Path, mime: str | None = None) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, p)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def get_file(self, key: str, path: Path) -> Path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), path)
        return Path(path)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def delete_prefix(self, prefix: str) -> None:
        target = self._path(prefix.rstrip("/"))
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


class S3Storage:
    def __init__(self, bucket: str, endpoint_url: str | None, region: str,
                 access_key: str | None = None, secret_key: str | None = None):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=endpoint_url, region_name=region,
                               aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                               config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))

    def upload_url(self, key: str, ttl: int, mime: str, max_bytes: int) -> tuple[str, dict, datetime]:
        url = self.s3.generate_presigned_url("put_object", Params={"Bucket": self.bucket, "Key": key,
                                                                    "ContentType": mime}, ExpiresIn=ttl)
        return url, {"Content-Type": mime}, datetime.fromtimestamp(time.time() + ttl, UTC)

    def read_url(self, key: str, ttl: int, mime: str | None = None, filename: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if mime:
            params["ResponseContentType"] = mime
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)

    def put_bytes(self, key: str, data: bytes, mime: str | None = None) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, **({"ContentType": mime} if mime else {}))

    def put_file(self, key: str, path: Path, mime: str | None = None) -> None:
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": mime} if mime else None)

    def get_bytes(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def get_file(self, key: str, path: Path) -> Path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.s3.download_file(self.bucket, key, str(path))
        return Path(path)

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.s3.exceptions.ClientError:
            return False

    def size(self, key: str) -> int:
        return self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"]

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})


def make_storage(settings) -> Storage:
    if settings.storage_backend == "s3":
        import os

        return S3Storage(settings.s3_bucket, settings.s3_endpoint_url, settings.s3_region,
                         os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY"))
    return LocalStorage(settings.storage_dir, settings.signing_secret)
