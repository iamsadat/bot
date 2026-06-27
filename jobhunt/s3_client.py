"""S3 client for storing resumes, cover letters, and other artifacts.

Provides abstraction over boto3 so the application can be tested offline
(FakeS3Client) or run with real S3/MinIO in production.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseS3Client(ABC):
    """Abstract S3 client interface."""

    @abstractmethod
    def upload_file(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload a file to S3. Returns the public URL (or path for testing)."""

    @abstractmethod
    def download_file(self, key: str) -> bytes | None:
        """Download a file from S3. Returns None if not found."""

    @abstractmethod
    def delete_file(self, key: str) -> None:
        """Delete a file from S3."""

    @abstractmethod
    def list_files(self, prefix: str = "") -> list[str]:
        """List all keys with the given prefix."""

    @abstractmethod
    def file_exists(self, key: str) -> bool:
        """Check if a file exists in S3."""


class S3Client(BaseS3Client):
    """Real S3 client using boto3."""

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        """Initialize S3 client.

        Args:
            bucket: S3 bucket name.
            region: AWS region.
            endpoint_url: Optional custom endpoint (for MinIO, etc).
            access_key: AWS access key (uses env/config if None).
            secret_key: AWS secret key (uses env/config if None).
        """
        import boto3

        self.bucket = bucket
        self.region = region

        session_kwargs = {}
        if access_key and secret_key:
            session_kwargs = {"aws_access_key_id": access_key, "aws_secret_access_key": secret_key}

        self.s3 = boto3.client("s3", region_name=region, endpoint_url=endpoint_url, **session_kwargs)

    def upload_file(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)
        if self.s3.meta.endpoint_url:
            # MinIO or custom endpoint
            return f"{self.s3.meta.endpoint_url}/{self.bucket}/{key}"
        # AWS S3
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{key}"

    def download_file(self, key: str) -> bytes | None:
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def delete_file(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def list_files(self, prefix: str = "") -> list[str]:
        response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        if "Contents" not in response:
            return []
        return [obj["Key"] for obj in response["Contents"]]

    def file_exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.s3.exceptions.NoSuchKey:
            return False


class FakeS3Client(BaseS3Client):
    """In-memory S3 mock for offline testing."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def upload_file(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        self._files[key] = content
        return f"s3://fake-bucket/{key}"

    def download_file(self, key: str) -> bytes | None:
        return self._files.get(key)

    def delete_file(self, key: str) -> None:
        self._files.pop(key, None)

    def list_files(self, prefix: str = "") -> list[str]:
        return [k for k in self._files.keys() if k.startswith(prefix)]

    def file_exists(self, key: str) -> bool:
        return key in self._files
