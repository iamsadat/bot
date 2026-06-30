"""Extended tests for jobhunt/s3_client.py — covering the real S3Client class
initialization and methods via mocking boto3 (previously at 57% coverage).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from jobhunt.s3_client import FakeS3Client

_has_boto3 = "boto3" in sys.modules or bool(__import__("importlib").util.find_spec("boto3"))

_skip_no_boto3 = pytest.mark.skipif(not _has_boto3, reason="boto3 not installed")

if _has_boto3:
    from jobhunt.s3_client import S3Client


# ----------------------------------------------------------------- S3Client init


@_skip_no_boto3
class TestS3ClientInit:
    def test_init_with_default_params(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="my-bucket")

            mock_boto3_client.assert_called_once_with(
                "s3", region_name="us-east-1", endpoint_url=None
            )
            assert client.bucket == "my-bucket"
            assert client.region == "us-east-1"

    def test_init_with_custom_region_and_endpoint(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_boto3_client.return_value = mock_s3

            client = S3Client(
                bucket="test-bucket",
                region="eu-west-1",
                endpoint_url="http://localhost:9000",
            )

            mock_boto3_client.assert_called_once_with(
                "s3", region_name="eu-west-1", endpoint_url="http://localhost:9000"
            )
            assert client.region == "eu-west-1"

    def test_init_with_credentials(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_boto3_client.return_value = mock_s3

            S3Client(
                bucket="secure-bucket",
                access_key="AKIAIOSFODNN7EXAMPLE",
                secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            )

            mock_boto3_client.assert_called_once_with(
                "s3",
                region_name="us-east-1",
                endpoint_url=None,
                aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
                aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            )


# ----------------------------------------------------------------- S3Client methods


@_skip_no_boto3
class TestS3ClientMethods:
    def test_upload_file_aws_url(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_s3.meta.endpoint_url = None
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="resumes", region="us-west-2")
            url = client.upload_file("user/resume.pdf", b"content", "application/pdf")

            mock_s3.put_object.assert_called_once_with(
                Bucket="resumes",
                Key="user/resume.pdf",
                Body=b"content",
                ContentType="application/pdf",
            )
            assert url == "https://resumes.s3.us-west-2.amazonaws.com/user/resume.pdf"

    def test_upload_file_custom_endpoint_url(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_s3.meta.endpoint_url = "http://minio:9000"
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="docs", endpoint_url="http://minio:9000")
            url = client.upload_file("doc.txt", b"text")

            assert url == "http://minio:9000/docs/doc.txt"

    def test_download_file_success(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_body = MagicMock()
            mock_body.read.return_value = b"file content"
            mock_s3.get_object.return_value = {"Body": mock_body}
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="bucket")
            result = client.download_file("path/to/file.pdf")

            assert result == b"file content"
            mock_s3.get_object.assert_called_once_with(Bucket="bucket", Key="path/to/file.pdf")

    def test_download_file_not_found(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
            mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey("not found")
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="bucket")
            result = client.download_file("missing.pdf")

            assert result is None

    def test_delete_file(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="bucket")
            client.delete_file("old/file.pdf")

            mock_s3.delete_object.assert_called_once_with(Bucket="bucket", Key="old/file.pdf")

    def test_list_files_with_contents(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_s3.list_objects_v2.return_value = {
                "Contents": [
                    {"Key": "resumes/a.pdf"},
                    {"Key": "resumes/b.pdf"},
                ]
            }
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="bucket")
            result = client.list_files("resumes/")

            assert result == ["resumes/a.pdf", "resumes/b.pdf"]
            mock_s3.list_objects_v2.assert_called_once_with(Bucket="bucket", Prefix="resumes/")

    def test_list_files_empty(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_s3.list_objects_v2.return_value = {}  # No "Contents" key
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="bucket")
            result = client.list_files("empty/")

            assert result == []

    def test_file_exists_true(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="bucket")
            result = client.file_exists("path/file.pdf")

            assert result is True
            mock_s3.head_object.assert_called_once_with(Bucket="bucket", Key="path/file.pdf")

    def test_file_exists_false(self):
        with patch("boto3.client") as mock_boto3_client:
            mock_s3 = MagicMock()
            mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
            mock_s3.head_object.side_effect = mock_s3.exceptions.NoSuchKey("nope")
            mock_boto3_client.return_value = mock_s3

            client = S3Client(bucket="bucket")
            result = client.file_exists("missing.pdf")

            assert result is False


# ----------------------------------------------------------------- FakeS3Client (additional edge cases)


class TestFakeS3ClientEdgeCases:
    def test_overwrite_existing_file(self):
        client = FakeS3Client()
        client.upload_file("file.txt", b"version1")
        client.upload_file("file.txt", b"version2")
        assert client.download_file("file.txt") == b"version2"

    def test_list_files_prefix_matching(self):
        client = FakeS3Client()
        client.upload_file("a/b/c.txt", b"1")
        client.upload_file("a/b/d.txt", b"2")
        client.upload_file("a/x/e.txt", b"3")

        assert len(client.list_files("a/b/")) == 2
        assert len(client.list_files("a/")) == 3
        assert len(client.list_files("a/x/")) == 1
        assert len(client.list_files("z/")) == 0

    def test_delete_then_check(self):
        client = FakeS3Client()
        client.upload_file("k", b"v")
        assert client.file_exists("k")
        client.delete_file("k")
        assert not client.file_exists("k")
        assert client.download_file("k") is None
