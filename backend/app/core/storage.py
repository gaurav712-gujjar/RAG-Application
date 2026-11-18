"""Raw-file storage. Local disk for dev/tests, S3-compatible (AWS S3 / MinIO) for production.

Methods are synchronous; callers run them with asyncio.to_thread.
Keys look like "<user_id>/<doc_id>.<ext>" and are never derived from user-supplied filenames.
"""
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Storage(Protocol):
    def ensure_ready(self) -> None: ...
    def save(self, key: str, data: bytes) -> None: ...
    def download(self, key: str, dest: str) -> None: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:  # blocks "../" and absolute keys
            raise ValueError("Invalid storage key")
        return path

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def download(self, key: str, dest: str) -> None:
        shutil.copyfile(self._path(key), dest)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, *, bucket: str, endpoint: str, access_key: str, secret_key: str, region: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region,
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def ensure_ready(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def save(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def download(self, key: str, dest: str) -> None:
        self.client.download_file(self.bucket, key, dest)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)  # idempotent


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3Storage(
            bucket=s.s3_bucket,
            endpoint=s.s3_endpoint,
            access_key=s.s3_access_key,
            secret_key=s.s3_secret_key,
            region=s.s3_region,
        )
    return LocalStorage(s.upload_dir)
