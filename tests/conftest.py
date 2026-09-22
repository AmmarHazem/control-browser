from __future__ import annotations

import pytest

from app.config import Settings
from tests.fixture_site.fake_llm import FakeLLM
from tests.fixture_site.server import FixtureSite
from tests.fixture_site.service import ServiceHarness


@pytest.fixture(scope="session")
def site():
    server = FixtureSite().start()
    yield server
    server.stop()


@pytest.fixture
def settings(tmp_path_factory) -> Settings:
    return Settings(
        api_keys=["test-key"],
        allow_private_addresses=True,
        navigation_min_interval_seconds=0.0,
        janitor_interval_seconds=3600.0,
        data_dir=tmp_path_factory.mktemp("data"),
        llm_api_key="fake",
        _env_file=None,
    )


@pytest.fixture
async def pool(settings):
    from app.browser.pool import SessionPool

    pool = SessionPool(settings)
    await pool.start()
    yield pool
    await pool.shutdown()


@pytest.fixture
async def session(pool):
    return await pool.open()


@pytest.fixture
def llm():
    fake = FakeLLM().start()
    yield fake
    fake.stop()


@pytest.fixture
def service(settings, llm):
    harness = ServiceHarness(
        settings.model_copy(update={"llm_base_url": llm.base_url, "llm_model": "fake-model"})
    ).start()
    yield harness
    harness.stop()
