import pytest
from httpx import ASGITransport, AsyncClient

from app.policy import network
from app.policy.network import DomainPolicy
from app.storage.downloads import DownloadStore, safe_filename


@pytest.mark.asyncio
async def test_direct_download_preserves_bytes_and_is_retrievable(tmp_path, site):
    payload = b"source bytes\x00\xff"
    (tmp_path / "source.bin").write_bytes(payload)
    # The fixture site serves static files from its own directory.
    from tests.fixture_site.server import PAGES

    source = PAGES / "download-test.bin"
    source.write_bytes(payload)
    try:
        store = DownloadStore(
            tmp_path / "downloads", max_bytes=1024, retention_seconds=86400, max_concurrent=2
        )
        started = store.start(
            site.url("/download-test.bin"), policy=DomainPolicy(), allow_private=True
        )
        record = await store.wait(started["id"])
        assert record["status"] == "completed"
        assert record["bytes_received"] == len(payload)
        assert store.path(started["id"]).read_bytes() == payload
    finally:
        source.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_size_limit_leaves_no_partial_file(tmp_path, site):
    from tests.fixture_site.server import PAGES

    source = PAGES / "download-too-large.bin"
    source.write_bytes(b"x" * 128)
    try:
        store = DownloadStore(
            tmp_path / "downloads", max_bytes=32, retention_seconds=86400, max_concurrent=1
        )
        started = store.start(
            site.url("/download-too-large.bin"), policy=DomainPolicy(), allow_private=True
        )
        record = await store.wait(started["id"])
        assert record["status"] == "failed"
        assert store.path(started["id"]) is None
        assert not (store.root / f"{started['id']}.part").exists()
    finally:
        source.unlink(missing_ok=True)


def test_filename_cannot_escape_storage():
    assert safe_filename("../../hello.exe") == "hello.exe"
    assert safe_filename("a\\b.txt") == "b.txt"


@pytest.mark.asyncio
async def test_html_redirect_is_not_a_download(tmp_path, site):
    store = DownloadStore(
        tmp_path / "downloads", max_bytes=1024, retention_seconds=86400, max_concurrent=1
    )
    started = store.start(site.url("/download-redirect"), policy=DomainPolicy(), allow_private=True)
    record = await store.wait(started["id"])
    assert record["status"] == "failed"
    assert "web page" in record["error"]
    assert store.path(started["id"]) is None


@pytest.mark.asyncio
async def test_expired_file_is_removed_after_restart(tmp_path, site):
    from tests.fixture_site.server import PAGES

    source = PAGES / "download-expiry.bin"
    source.write_bytes(b"hello")
    try:
        root = tmp_path / "downloads"
        store = DownloadStore(root, max_bytes=1024, retention_seconds=86400, max_concurrent=1)
        started = store.start(
            site.url("/download-expiry.bin"), policy=DomainPolicy(), allow_private=True
        )
        assert (await store.wait(started["id"]))["status"] == "completed"
        restarted = DownloadStore(root, max_bytes=1024, retention_seconds=0, max_concurrent=1)
        assert restarted.path(started["id"]) is None
        assert restarted.get(started["id"])["status"] == "expired"
    finally:
        source.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_cancel_pending_download_removes_partial_file(tmp_path, site):
    store = DownloadStore(
        tmp_path / "downloads", max_bytes=1024, retention_seconds=86400, max_concurrent=1
    )
    await store._slots.acquire()
    try:
        started = store.start(site.url("/index.html"), policy=DomainPolicy(), allow_private=True)
        assert await store.cancel(started["id"])
        assert store.get(started["id"])["status"] == "cancelled"
        assert not (store.root / f"{started['id']}.part").exists()
    finally:
        store._slots.release()


@pytest.mark.asyncio
async def test_total_quota_reserves_capacity_for_active_transfers(tmp_path, site):
    store = DownloadStore(
        tmp_path / "downloads",
        max_bytes=32,
        total_bytes_cap=32,
        retention_seconds=86400,
        max_concurrent=2,
    )
    await store._slots.acquire()
    await store._slots.acquire()
    try:
        started = store.start(site.url("/index.html"), policy=DomainPolicy(), allow_private=True)
        with pytest.raises(ValueError, match="quota"):
            store.start(site.url("/index.html"), policy=DomainPolicy(), allow_private=True)
        await store.cancel(started["id"])
    finally:
        store._slots.release()
        store._slots.release()


@pytest.mark.asyncio
async def test_dns_rebinding_is_blocked_at_connection(tmp_path, monkeypatch):
    calls = 0

    async def changing_dns(host, port):
        nonlocal calls
        calls += 1
        return ["93.184.215.14"] if calls == 1 else ["127.0.0.1"]

    monkeypatch.setattr(network, "resolve_addresses", changing_dns)
    monkeypatch.setattr("app.storage.downloads.resolve_addresses", changing_dns)
    store = DownloadStore(
        tmp_path / "downloads", max_bytes=32, retention_seconds=86400, max_concurrent=1
    )
    started = store.start(
        "http://rebind.example/test.bin", policy=DomainPolicy(), allow_private=False
    )
    record = await store.wait(started["id"])
    assert record["status"] == "failed"
    assert calls >= 2
    assert "blocked address" in record["error"]


@pytest.mark.asyncio
async def test_retrieval_is_public_by_id_but_status_requires_api_key(tmp_path, site):
    from types import SimpleNamespace

    from app.config import Settings
    from app.main import create_app
    from tests.fixture_site.server import PAGES

    source = PAGES / "download-retrieval.bin"
    payload = b"original\x00bytes"
    source.write_bytes(payload)
    try:
        store = DownloadStore(
            tmp_path / "downloads", max_bytes=1024, retention_seconds=86400, max_concurrent=1
        )
        started = store.start(
            site.url("/download-retrieval.bin"), policy=DomainPolicy(), allow_private=True
        )
        assert (await store.wait(started["id"]))["status"] == "completed"
        app = create_app(Settings(api_keys=["secret"], data_dir=tmp_path, _env_file=None))
        app.state.container = SimpleNamespace(downloads=store)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(started["retrieval_url"])
            assert response.status_code == 200
            assert response.content == payload
            assert response.headers["cache-control"] == "private, no-store"
            status = await client.get(f"/downloads/{started['id']}/status")
            assert status.status_code == 401
            status = await client.get(
                f"/downloads/{started['id']}/status", headers={"x-api-key": "secret"}
            )
            assert status.json()["status"] == "completed"
    finally:
        source.unlink(missing_ok=True)
