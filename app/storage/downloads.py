from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import aiohttp
from aiohttp.abc import ResolveResult
from playwright.async_api import Download

from app.policy.network import (
    DomainPolicy,
    NetworkPolicyError,
    check_url,
    is_blocked_address,
    resolve_addresses,
)


class GuardedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, allow_private: bool) -> None:
        self.allow_private = allow_private

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        addresses = await resolve_addresses(host, port)
        if not addresses:
            raise NetworkPolicyError(f"{host} did not resolve")
        if not self.allow_private:
            for address in addresses:
                if is_blocked_address(address):
                    raise NetworkPolicyError(f"{host} resolves to a blocked address")
        return [
            ResolveResult(
                hostname=host,
                host=address,
                port=port,
                family=socket.AF_INET6 if ":" in address else socket.AF_INET,
                proto=socket.IPPROTO_TCP,
                flags=0,
            )
            for address in addresses
        ]

    async def close(self) -> None:
        pass


def safe_filename(value: str) -> str:
    name = unquote(value).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w .()\-]+", "_", name).strip(" .")
    return name[:180] or "download"


class DownloadStore:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        retention_seconds: float,
        max_concurrent: int,
        total_bytes_cap: int | None = None,
        finalization_seconds: float = 3600.0,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.total_bytes_cap = total_bytes_cap or max_bytes * max_concurrent
        self.retention_seconds = retention_seconds
        self.finalization_seconds = finalization_seconds
        self._slots = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, asyncio.Task] = {}
        self._records: dict[str, dict] = {}
        for entry in root.glob("*.json"):
            try:
                record = json.loads(entry.read_text())
                if record["status"] == "expired":
                    entry.unlink(missing_ok=True)
                    continue
                if record["status"] in ("pending", "running"):
                    record["status"] = "failed"
                    record["error"] = "service restarted during transfer"
                    self._save(record)
                self._records[record["id"]] = record
            except (ValueError, KeyError):
                continue
        for partial in root.glob("*.part"):
            partial.unlink(missing_ok=True)
        for temporary in root.glob("*.tmp"):
            temporary.unlink(missing_ok=True)
        for completed in root.glob("*.bin"):
            if self._records.get(completed.stem, {}).get("status") != "completed":
                completed.unlink(missing_ok=True)

    def _save(self, record: dict) -> None:
        target = self.root / f"{record['id']}.json"
        temporary = self.root / f"{record['id']}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps(record))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, file_id: str) -> dict | None:
        record = self._records.get(file_id)
        timestamp = (
            (record.get("completed_at") or record["created_at"])
            if record and record["status"] in ("completed", "failed", "cancelled")
            else None
        )
        if record and timestamp is not None and time.time() - timestamp >= self.retention_seconds:
            self._expire(record)
        return dict(record) if record else None

    def path(self, file_id: str) -> Path | None:
        record = self.get(file_id)
        if record and record["status"] == "completed":
            path = self.root / f"{file_id}.bin"
            return path if path.is_file() else None
        return None

    def _expire(self, record: dict) -> None:
        file_id = record["id"]
        if record["status"] == "expired":
            return
        task = self._tasks.get(file_id)
        if task and not task.done():
            task.cancel()
        for suffix in (".bin", ".part"):
            (self.root / f"{file_id}{suffix}").unlink(missing_ok=True)
        record["status"] = "expired"
        self._save(record)

    async def sweep_forever(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            self.sweep()

    def sweep(self) -> None:
        for file_id in list(self._records):
            record = self.get(file_id)
            task = self._tasks.get(file_id)
            if task and task.done():
                self._tasks.pop(file_id, None)
            if record and record["status"] == "expired":
                self._records.pop(file_id, None)
                (self.root / f"{file_id}.json").unlink(missing_ok=True)

    def _new_record(self, url: str) -> dict:
        self.sweep()
        reserved = sum(
            self.max_bytes
            if r["status"] in ("pending", "running")
            else r["bytes_received"]
            if r["status"] == "completed"
            else 0
            for r in self._records.values()
        )
        if reserved + self.max_bytes > self.total_bytes_cap:
            raise ValueError("download storage quota is full")
        file_id = uuid.uuid4().hex
        record = {
            "id": file_id,
            "source_url": url,
            "status": "pending",
            "filename": None,
            "media_type": None,
            "bytes_received": 0,
            "total_bytes": None,
            "created_at": time.time(),
            "completed_at": None,
            "error": None,
            "retrieval_url": f"/downloads/{file_id}",
        }
        self._records[file_id] = record
        self._save(record)
        return record

    def start(
        self,
        url: str,
        *,
        policy: DomainPolicy,
        allow_private: bool,
        headers: dict[str, str] | None = None,
        on_progress=None,
    ) -> dict:
        record = self._new_record(url)
        self._tasks[record["id"]] = asyncio.create_task(
            self._transfer(record, policy, allow_private, headers or {}, on_progress)
        )
        return dict(record)

    def start_browser(
        self, download: Download, *, media_type: str | None = None, on_progress=None
    ) -> dict:
        record = self._new_record(download.url)
        record["filename"] = safe_filename(download.suggested_filename)
        record["media_type"] = media_type or "application/octet-stream"
        self._save(record)
        self._tasks[record["id"]] = asyncio.create_task(
            self._save_browser_download(record, download, on_progress)
        )
        return dict(record)

    async def _save_browser_download(self, record: dict, download: Download, on_progress) -> None:
        partial = self.root / f"{record['id']}.part"
        try:
            async with self._slots:
                record["status"] = "running"
                await asyncio.to_thread(self._save, dict(record))
                transfer = asyncio.create_task(download.save_as(partial))
                try:
                    while not transfer.done():
                        await asyncio.sleep(0.1)
                        size = await asyncio.to_thread(
                            lambda: partial.stat().st_size if partial.exists() else 0
                        )
                        if size > self.max_bytes:
                            await download.cancel()
                            raise ValueError("file exceeds the configured size limit")
                        if size > record["bytes_received"]:
                            record["bytes_received"] = size
                            if on_progress:
                                try:
                                    await on_progress(dict(record))
                                except Exception:
                                    pass
                    await transfer
                finally:
                    if not transfer.done():
                        transfer.cancel()
                    await asyncio.gather(transfer, return_exceptions=True)
                size = await asyncio.to_thread(lambda: partial.stat().st_size)
                if size == 0 or size > self.max_bytes:
                    raise ValueError("empty file or file exceeds the configured size limit")
                record["bytes_received"] = size
                record["total_bytes"] = size
                await asyncio.to_thread(os.replace, partial, self.root / f"{record['id']}.bin")
                record["status"] = "completed"
                record["completed_at"] = time.time()
                if on_progress:
                    try:
                        await on_progress(dict(record))
                    except Exception:
                        pass
        except asyncio.CancelledError:
            await download.cancel()
            if record["status"] != "expired":
                record["status"] = "cancelled"
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
        finally:
            partial.unlink(missing_ok=True)
            await asyncio.to_thread(self._save, dict(record))

    async def wait(self, file_id: str) -> dict | None:
        task = self._tasks.get(file_id)
        if task:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
        return self.get(file_id)

    async def cancel(self, file_id: str) -> bool:
        task = self._tasks.get(file_id)
        if not task or task.done():
            return False
        task.cancel()
        await self.wait(file_id)
        record = self._records[file_id]
        if record["status"] in ("pending", "running"):
            record["status"] = "cancelled"
            (self.root / f"{file_id}.part").unlink(missing_ok=True)
            self._save(record)
        return True

    async def _transfer(
        self,
        record: dict,
        policy: DomainPolicy,
        allow_private: bool,
        headers: dict[str, str],
        on_progress,
    ) -> None:
        file_id = record["id"]
        partial = self.root / f"{file_id}.part"
        try:
            async with self._slots:
                record["status"] = "running"
                self._save(record)
                connector = aiohttp.TCPConnector(
                    resolver=GuardedResolver(allow_private), use_dns_cache=False
                )
                timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
                async with aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    trust_env=False,
                    auto_decompress=False,
                ) as client:
                    url = record["source_url"]
                    for _ in range(10):
                        await check_url(url, policy, allow_private=allow_private)
                        async with client.get(
                            url,
                            headers={"Accept-Encoding": "identity", **headers},
                            allow_redirects=False,
                        ) as response:
                            if response.status in (301, 302, 303, 307, 308):
                                location = response.headers.get("location")
                                if not location:
                                    raise ValueError("redirect has no location")
                                next_url = urljoin(url, location)
                                if (
                                    urlsplit(next_url).netloc != urlsplit(url).netloc
                                    or urlsplit(next_url).scheme != urlsplit(url).scheme
                                ):
                                    headers = {
                                        k: v
                                        for k, v in headers.items()
                                        if k.lower() not in ("cookie", "authorization", "referer")
                                    }
                                url = next_url
                                continue
                            response.raise_for_status()
                            content_type = (
                                response.headers.get("content-type", "").split(";")[0].lower()
                            )
                            if content_type in ("text/html", "application/xhtml+xml"):
                                raise ValueError("source returned a web page, not a direct file")
                            length = response.headers.get("content-length")
                            record["total_bytes"] = (
                                int(length) if length and length.isdigit() else None
                            )
                            if (
                                record["total_bytes"] is not None
                                and record["total_bytes"] > self.max_bytes
                            ):
                                raise ValueError("file exceeds the configured size limit")
                            disposition = response.headers.get("content-disposition", "")
                            match = re.search(
                                r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I
                            )
                            record["filename"] = safe_filename(
                                match.group(1) if match else urlsplit(url).path
                            )
                            record["media_type"] = content_type or "application/octet-stream"
                            record["source_url"] = url
                            last_persisted = 0
                            with partial.open("wb") as output:
                                async for chunk in response.content.iter_chunked(64 * 1024):
                                    record["bytes_received"] += len(chunk)
                                    if record["bytes_received"] > self.max_bytes:
                                        raise ValueError("file exceeds the configured size limit")
                                    await asyncio.to_thread(output.write, chunk)
                                    if record["bytes_received"] - last_persisted >= 1024 * 1024:
                                        await asyncio.to_thread(self._save, dict(record))
                                        last_persisted = record["bytes_received"]
                                    if on_progress:
                                        try:
                                            await on_progress(dict(record))
                                        except Exception:
                                            pass
                            if not record["bytes_received"]:
                                raise ValueError("source returned an empty file")
                            await asyncio.to_thread(
                                os.replace, partial, self.root / f"{file_id}.bin"
                            )
                            record["status"] = "completed"
                            record["completed_at"] = time.time()
                            return
                    raise ValueError("too many redirects")
        except asyncio.CancelledError:
            if record["status"] != "expired":
                record["status"] = "cancelled"
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
        finally:
            partial.unlink(missing_ok=True)
            await asyncio.to_thread(self._save, dict(record))
