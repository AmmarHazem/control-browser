import pytest

from app.policy.network import DomainPolicy
from app.storage.downloads import DownloadStore
from tests.fixture_site.server import PAGES

pytestmark = pytest.mark.integration


async def test_download_url_and_ref_share_store(session, site, tmp_path):
    session.downloads = DownloadStore(
        tmp_path / "downloads", max_bytes=1024, retention_seconds=86400, max_concurrent=2
    )
    direct = await session.download_url(site.url("/download-file"))
    assert direct.ok
    first = await session.downloads.wait(direct.data["download"]["id"])
    assert first["status"] == "completed"
    assert session.downloads.path(first["id"]).read_bytes() == b"fixture file\x00\xff"

    page = PAGES / "download-actions.html"
    page.write_text('<a href="/download-file">Get fixture file</a>')
    try:
        assert (await session.navigate(site.url("/download-actions.html"))).ok
        line = next(
            line for line in session.last_snapshot.splitlines() if "Get fixture file" in line
        )
        ref = line.split("[ref=")[1].split("]")[0]
        clicked = await session.download_ref(ref)
        assert clicked.ok, clicked.error
        second = await session.downloads.wait(clicked.data["download"]["id"])
        assert second["status"] == "completed"
        assert second["filename"] == "fixture.bin"
        assert session.downloads.path(second["id"]).read_bytes() == b"fixture file\x00\xff"
    finally:
        page.unlink(missing_ok=True)


async def test_blocked_download_url(session, site, tmp_path):
    session.downloads = DownloadStore(
        tmp_path / "downloads", max_bytes=1024, retention_seconds=86400, max_concurrent=1
    )
    session.domain_policy = DomainPolicy(blocked=["127.0.0.1"])
    result = await session.download_url(site.url("/download-file"))
    assert not result.ok
    assert "blocked" in result.error


async def test_download_ref_requires_store_before_click(session, site):
    assert (await session.navigate(site.url("/form.html"))).ok
    line = next(
        line for line in session.last_snapshot.splitlines() if 'button "Send message"' in line
    )
    ref = line.split("[ref=")[1].split("]")[0]
    result = await session.download_ref(ref)
    assert not result.ok
    assert "not configured" in result.error
    assert "/form.html" in (await session.page()).url


async def test_download_ref_uses_structural_risk_gate(session, site, tmp_path):
    from app.browser.session import ActionRejected

    session.downloads = DownloadStore(
        tmp_path / "downloads", max_bytes=1024, retention_seconds=86400, max_concurrent=1
    )
    assert (await session.navigate(site.url("/form.html"))).ok
    line = next(
        line for line in session.last_snapshot.splitlines() if 'button "Send message"' in line
    )
    ref = line.split("[ref=")[1].split("]")[0]
    seen = []

    async def reject(finding, action, context):
        seen.append((finding.source, action))
        raise ActionRejected("confirmation denied")

    session.risk_gate = reject
    with pytest.raises(ActionRejected, match="confirmation denied"):
        await session.download_ref(ref)
    assert seen[0][1] == "download_ref"
    assert "/form.html" in (await session.page()).url
