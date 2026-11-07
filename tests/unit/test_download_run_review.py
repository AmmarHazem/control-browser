import asyncio
import time
from types import SimpleNamespace

import pytest

from app.agent.loop import RunLoop
from app.agent.run import Outcome, Run, RunSpec
from app.policy.network import DomainPolicy
from app.storage.downloads import DownloadStore


class Repository:
    async def save(self, record):
        pass

    async def append_event(self, run_id, event):
        pass


@pytest.fixture(autouse=True)
def no_model_client(monkeypatch):
    monkeypatch.setattr("app.agent.loop.AsyncOpenAI", lambda **kwargs: object())


def make_loop(task, downloads=None):
    run = Run(RunSpec(task=task), Repository())
    session = SimpleNamespace(visited_urls=[], downloads=downloads)
    return RunLoop(run, session)


@pytest.mark.asyncio
async def test_download_word_in_research_task_does_not_require_file():
    loop = make_loop("find the download size of Node 22")
    assert await loop._handle_done({}, {"result": "the page lists the size"})
    assert loop.run.record.outcome == "completed"


@pytest.mark.asyncio
async def test_transfer_finalization_has_budget_after_agent_deadline(tmp_path, site):
    store = DownloadStore(
        tmp_path / "downloads",
        max_bytes=1024,
        retention_seconds=86400,
        max_concurrent=1,
        finalization_seconds=2,
    )
    await store._slots.acquire()
    started = store.start(site.url("/download-file"), policy=DomainPolicy(), allow_private=True)
    loop = make_loop("save a fixture", store)
    loop._download_ids = [started["id"]]
    loop._selected_download_ids = [started["id"]]
    loop._deadline = time.monotonic() - 1

    async def release():
        await asyncio.sleep(0.05)
        store._slots.release()

    release_task = asyncio.create_task(release())
    await loop._finish(Outcome.COMPLETED, result="saved")
    await release_task
    assert loop.run.record.outcome == "completed"
    assert loop.run.record.downloads[0]["status"] == "completed"
