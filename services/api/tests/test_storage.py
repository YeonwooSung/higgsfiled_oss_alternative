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
