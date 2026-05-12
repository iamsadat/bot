"""Tests for S3 client abstraction (artifact storage).

Tests both FakeS3Client (offline) and validate the interface contract.
Real S3 tests are integration tests (require S3 or MinIO).
"""

import pytest
from jobhunt.s3_client import FakeS3Client


@pytest.fixture
def s3_client():
    """Return a fake S3 client for offline tests."""
    return FakeS3Client()


def test_upload_and_download(s3_client):
    """Test basic file upload and download."""
    content = b"Senior Backend Engineer at Acme Corp"
    key = "resumes/u-123/acme-backend.pdf"

    url = s3_client.upload_file(key, content, content_type="application/pdf")
    assert "acme-backend.pdf" in url

    downloaded = s3_client.download_file(key)
    assert downloaded == content


def test_download_nonexistent_file(s3_client):
    """Test downloading a file that doesn't exist."""
    result = s3_client.download_file("nonexistent/file.pdf")
    assert result is None


def test_delete_file(s3_client):
    """Test deleting a file."""
    key = "resumes/u-456/test.pdf"
    s3_client.upload_file(key, b"content")

    assert s3_client.file_exists(key)
    s3_client.delete_file(key)
    assert not s3_client.file_exists(key)


def test_file_exists(s3_client):
    """Test file existence check."""
    key = "documents/u-789/cover-letter.pdf"

    assert not s3_client.file_exists(key)
    s3_client.upload_file(key, b"content")
    assert s3_client.file_exists(key)


def test_list_files_with_prefix(s3_client):
    """Test listing files by prefix."""
    s3_client.upload_file("resumes/u-123/acme.pdf", b"resume1")
    s3_client.upload_file("resumes/u-123/globex.pdf", b"resume2")
    s3_client.upload_file("cover-letters/u-123/acme.pdf", b"cover1")

    resumes = s3_client.list_files("resumes/u-123/")
    assert len(resumes) == 2
    assert all(k.startswith("resumes/u-123/") for k in resumes)

    cover_letters = s3_client.list_files("cover-letters/")
    assert len(cover_letters) == 1


def test_list_files_empty_prefix(s3_client):
    """Test listing all files."""
    s3_client.upload_file("file1.txt", b"content1")
    s3_client.upload_file("file2.txt", b"content2")

    all_files = s3_client.list_files("")
    assert len(all_files) == 2


def test_upload_multiple_files(s3_client):
    """Test uploading multiple files."""
    files = {
        "u-123/resume.pdf": b"resume content",
        "u-123/cover-letter.pdf": b"cover letter",
        "u-456/resume.pdf": b"another resume",
    }

    for key, content in files.items():
        s3_client.upload_file(key, content)

    for key, content in files.items():
        assert s3_client.download_file(key) == content


def test_delete_nonexistent_file(s3_client):
    """Test deleting a file that doesn't exist (should be safe)."""
    s3_client.delete_file("nonexistent.pdf")  # Should not raise


def test_upload_empty_file(s3_client):
    """Test uploading an empty file."""
    key = "empty.txt"
    s3_client.upload_file(key, b"")

    downloaded = s3_client.download_file(key)
    assert downloaded == b""


def test_upload_large_content(s3_client):
    """Test uploading large binary content."""
    # Simulate a PDF file (1 MB)
    large_content = b"x" * (1024 * 1024)
    key = "documents/large.pdf"

    url = s3_client.upload_file(key, large_content, content_type="application/pdf")
    downloaded = s3_client.download_file(key)
    assert len(downloaded) == len(large_content)
    assert downloaded == large_content


def test_content_type_preserved(s3_client):
    """Test that content type is stored (for real S3 usage)."""
    # FakeS3Client doesn't store metadata, but real S3 does
    # Just verify the method signature works with different content types
    s3_client.upload_file("doc.pdf", b"pdf content", content_type="application/pdf")
    s3_client.upload_file("resume.docx", b"docx content", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    s3_client.upload_file("cover.txt", b"text content", content_type="text/plain")

    assert s3_client.file_exists("doc.pdf")
    assert s3_client.file_exists("resume.docx")
    assert s3_client.file_exists("cover.txt")
