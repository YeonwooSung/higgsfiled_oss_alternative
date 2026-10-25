from oc_api.storage import LocalStorage, S3Storage, object_key


def test_object_key_is_uuid_prefixed_and_ignores_filename():
    key = object_key("s1", "p1", "a1", "product", "image/png")
    assert key == "s1/p1/a1/product.png"


def test_local_storage_roundtrip(tmp_path):
    s = LocalStorage(tmp_path, "secret-123")
    s.put_bytes("a/b/c.png", b"hello")
    assert s.exists("a/b/c.png") and s.size("a/b/c.png") == 5
    assert s.get_bytes("a/b/c.png") == b"hello"
    s.delete_prefix("a/")
    assert not s.exists("a/b/c.png")


def test_local_storage_rejects_path_traversal(tmp_path):
    s = LocalStorage(tmp_path / "root", "secret-123")
    import pytest
    with pytest.raises(ValueError):
        s.put_bytes("../escape.txt", b"x")


def test_s3_presigned_urls_are_short_lived():
    s = S3Storage(bucket="b", endpoint_url="http://minio:9000", region="us-east-1",
                  access_key="AKIAEXAMPLE", secret_key="secret")
    url, headers, _ = s.upload_url("s/p/a/product.png", ttl=600, mime="image/png", max_bytes=10)
    assert "X-Amz-Expires=600" in url and "/b/s/p/a/product.png" in url
    assert headers == {"Content-Type": "image/png"}
    assert "X-Amz-Expires=300" in s.read_url("s/p/a/product.png", 300)


def test_s3_presigned_urls_use_the_public_endpoint():
    """In compose the API talks to http://minio:9000, but the browser opens the presigned URLs on localhost."""
    s = S3Storage(bucket="b", endpoint_url="http://minio:9000", region="us-east-1",
                  access_key="AKIAEXAMPLE", secret_key="secret", public_endpoint_url="http://localhost:9000")
    url, _, _ = s.upload_url("s/p/a/product.png", ttl=600, mime="image/png", max_bytes=10)
    assert url.startswith("http://localhost:9000/b/s/p/a/product.png?")
    assert s.read_url("s/p/a/product.png", 300).startswith("http://localhost:9000/b/s/p/a/product.png?")
    assert s.s3.meta.endpoint_url == "http://minio:9000"


def test_local_read_url_supports_range_requests(app):
    """Result videos must be seekable in the browser: the local read route answers Range with 206."""
    from fastapi.testclient import TestClient

    storage = app.state.storage
    storage.put_bytes("s/p/a/geometry.mp4", bytes(range(100)), "video/mp4")
    url = storage.read_url("s/p/a/geometry.mp4", 60, mime="video/mp4", filename="camera-sketch_geometry.mp4")
    c = TestClient(app)
    full = c.get(url)
    assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes" and len(full.content) == 100
    assert full.headers["content-disposition"] == 'attachment; filename="camera-sketch_geometry.mp4"'
    part = c.get(url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == bytes(range(10, 20))
    assert part.headers["content-range"] == "bytes 10-19/100"


def test_concurrent_writes_to_one_key_use_separate_temp_files(tmp_path):
    """Two PUTs of the same key stream into distinct temp files (same directory); each commit is an atomic rename."""
    s = LocalStorage(tmp_path, "secret-123")
    a, b = s.begin_write("s/p/a/clip.mp4"), s.begin_write("s/p/a/clip.mp4")
    assert a != b and a.parent == b.parent == tmp_path / "s/p/a"
    a.write_bytes(b"first")
    b.write_bytes(b"second")
    s.commit_write("s/p/a/clip.mp4", a)
    assert s.get_bytes("s/p/a/clip.mp4") == b"first" and b.read_bytes() == b"second"  # b is untouched by a's commit
    s.commit_write("s/p/a/clip.mp4", b)
    assert s.get_bytes("s/p/a/clip.mp4") == b"second"
    assert sorted(p.name for p in (tmp_path / "s/p/a").iterdir()) == ["clip.mp4"]  # no temp file left behind


def test_local_copy_is_byte_identical_and_independent(tmp_path):
    s = LocalStorage(tmp_path, "secret-123")
    s.put_bytes("s/p/a/out.mp4", b"abc")
    s.copy("s/p/a/out.mp4", "s/q/b/product.mp4", "video/mp4")
    assert s.get_bytes("s/q/b/product.mp4") == b"abc"
    s.delete_prefix("s/p/a/out.mp4")
    assert not s.exists("s/p/a/out.mp4") and s.get_bytes("s/q/b/product.mp4") == b"abc"
    assert sorted(p.name for p in (tmp_path / "s/q/b").iterdir()) == ["product.mp4"]  # no temp file left behind


def test_local_copy_missing_source_raises(tmp_path):
    import pytest
    s = LocalStorage(tmp_path, "secret-123")
    with pytest.raises(FileNotFoundError):
        s.copy("nope/x.png", "dst/y.png")
    assert not s.exists("dst/y.png")


def test_s3_copy_calls_copy_object():
    from unittest.mock import MagicMock

    s = S3Storage(bucket="b", endpoint_url="http://minio:9000", region="us-east-1",
                  access_key="AKIAEXAMPLE", secret_key="secret")
    s.s3 = MagicMock()
    s.copy("s/p/a/out.mp4", "s/q/b/product.mp4", "video/mp4")
    s.s3.copy_object.assert_called_once_with(Bucket="b", Key="s/q/b/product.mp4",
                                             CopySource={"Bucket": "b", "Key": "s/p/a/out.mp4"},
                                             MetadataDirective="REPLACE", ContentType="video/mp4")
    s.s3.reset_mock()
    s.copy("s/p/a/out.mp4", "s/q/b/other.bin")
    assert "ContentType" not in s.s3.copy_object.call_args.kwargs
