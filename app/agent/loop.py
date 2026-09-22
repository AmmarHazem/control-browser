from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import jsonschema
from openai import APIStatusError, AsyncOpenAI

from app.agent.prompts import DONE_TOOL, GIVE_UP_TOOL, build_messages
from app.agent.run import (
    EVENT_OBSERVATION_LIMIT,
    Autonomy,
    Outcome,
    PendingConfirmation,
    Run,
    RunAborted,
    RunStatus,
    RunTimedOut,
    Vision,
)
from app.browser.session import ActionRejected, ActionResult, Session
from app.browser.toolset import dispatch, tool_specs
from app.policy.risky import RiskFinding
from app.storage.screenshots import ScreenshotStore

logger = logging.getLogger(__name__)

NUDGE = (
    "You did not call a tool. Call done() with the answer, or give_up() with the reason, "
    "or keep working with a browser tool."
)


class RunLoop:
    def __init__(
        self,
        run: Run,
        session: Session,
        *,
        search_client: Any = None,
        screenshots: ScreenshotStore | None = None,
        stuck_threshold: int = 2,
    ) -> None:
        self.run = run
        self.session = session
        self._search = search_client
        self._screenshots = screenshots
        self._stuck_threshold = stuck_threshold

        spec = run.spec
        self._client = AsyncOpenAI(api_key=spec.api_key or "unset", base_url=spec.base_url)
        self._vision_on = spec.vision is not Vision.OFF
        self._messages = build_messages(spec.task, spec.output_schema)
        self._tools = self._build_tools()
        self._step = 0
        self._consecutive_failures = 0
        self._nudged = False
        self._deadline = time.monotonic() + spec.timeout_seconds

    # ---- entry point -----------------------------------------------------

    async def execute(self) -> None:
        run = self.run
        self.session.risk_gate = self._risk_gate
        await run.emit("run.started", {"task": run.spec.task, "session_id": self.session.id})
        try:
            await self._drive()
        except RunTimedOut as exc:
            await self._finish(Outcome.FAILED, error=str(exc))
        except RunAborted as exc:
            await self._finish(Outcome.ABORTED, error=str(exc))
        except asyncio.CancelledError:
            await self._finish(Outcome.ABORTED, error="run was cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - reported as the run's outcome
            logger.exception("run failed", extra={"run_id": run.id})
            await self._finish(Outcome.FAILED, error=f"{type(exc).__name__}: {exc}")
        finally:
            self.session.risk_gate = None

    async def _drive(self) -> None:
        spec = self.run.spec
        while True:
            self._check_interrupts()
            if self._step >= spec.max_steps:
                await self._finish(
                    Outcome.FAILED, error=f"step limit of {spec.max_steps} steps reached"
                )
                return
            self._step += 1
            self.run.record.steps = self._step
            await self.run.emit("step.started", {"step": self._step})

            message = await self._complete()
            if message is None:
                return

            if message.get("content"):
                await self.run.emit(
                    "step.reasoning", {"step": self._step, "text": message["content"]}
                )

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                if await self._handle_no_tool_call(message.get("content") or ""):
                    return
                continue

            self._messages.append(message)
            for call in tool_calls:
                finished = await self._run_tool_call(call)
                if finished:
                    return

    def _check_interrupts(self) -> None:
        if self.run.aborted:
            raise RunAborted("the caller aborted this run")
        if time.monotonic() > self._deadline:
            raise RunTimedOut("the run exceeded its time limit")

    # ---- llm -------------------------------------------------------------

    async def _complete(self) -> dict[str, Any] | None:
        try:
            response = await self._client.chat.completions.create(
                model=self.run.spec.model,
                messages=self._messages,
                tools=self._tools,
                tool_choice="auto",
            )
        except APIStatusError as exc:
            if self._is_image_rejection(exc) and self.run.spec.vision is Vision.AUTO:
                await self._disable_vision()
                return await self._complete()
            await self._finish(Outcome.FAILED, error=f"the model endpoint failed: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 - reported as the run's outcome
            await self._finish(Outcome.FAILED, error=f"the model endpoint failed: {exc}")
            return None

        choice = response.choices[0].message
        payload: dict[str, Any] = {"role": "assistant", "content": choice.content}
        if choice.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in choice.tool_calls
            ]
        return payload

    def _is_image_rejection(self, exc: APIStatusError) -> bool:
        text = str(exc).lower()
        return any(word in text for word in ("image", "vision", "multimodal"))

    async def _disable_vision(self) -> None:
        self._vision_on = False
        self._messages = [_strip_images(message) for message in self._messages]
        await self.run.emit(
            "vision.disabled",
            {"reason": "the model endpoint rejected image content; continuing without screenshots"},
        )

    async def _handle_no_tool_call(self, content: str) -> bool:
        if self.run.spec.output_schema is None and content.strip():
            await self._finish(Outcome.COMPLETED, result=content.strip())
            return True
        if self._nudged:
            await self._finish(
                Outcome.FAILED, error="the model stopped without calling done() or give_up()"
            )
            return True
        self._nudged = True
        self._messages.append({"role": "assistant", "content": content})
        self._messages.append({"role": "user", "content": NUDGE})
        return False

    # ---- tools -----------------------------------------------------------

    def _build_tools(self) -> list[dict[str, Any]]:
        specs = tool_specs(include_search=self._search is not None)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.json_schema(),
                },
            }
            for spec in specs
        ]
        tools.extend([DONE_TOOL, GIVE_UP_TOOL])
        return tools

    async def _run_tool_call(self, call: dict[str, Any]) -> bool:
        name = call["function"]["name"]
        raw_args = call["function"].get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            await self._reply(call, {"ok": False, "error": f"arguments were not valid JSON: {raw_args}"})
            return False

        await self.run.emit("action.called", {"step": self._step, "action": name, "args": args})

        if name == "done":
            return await self._handle_done(call, args)
        if name == "give_up":
            reason = str(args.get("reason") or "no reason given")
            outcome = Outcome.BLOCKED if args.get("blocked") else Outcome.FAILED
            await self._finish(outcome, error=reason)
            return True

        result = await self._call_action(name, args)
        await self._record_result(name, result)
        await self._reply(call, result.as_dict())

        if result.screenshot and self._vision_on:
            await self._attach_image(result.screenshot)
        return False

    async def _call_action(self, name: str, args: dict[str, Any]) -> ActionResult:
        self._check_interrupts()
        try:
            result = await dispatch(
                name, args, session=self.session, search_client=self._search
            )
        except ActionRejected as exc:
            return ActionResult(ok=False, error=str(exc))
        except KeyError as exc:
            return ActionResult(ok=False, error=f"missing argument {exc}")
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, not fatal
            logger.warning("action raised", extra={"run_id": self.run.id, "action": name})
            return ActionResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        if result.ok and not result.screenshot and self._should_look():
            shot = await self.session.screenshot()
            if shot.ok:
                result.screenshot = shot.screenshot
        return result

    async def _record_result(self, name: str, result: ActionResult) -> None:
        self._consecutive_failures = 0 if result.ok else self._consecutive_failures + 1
        await self.run.emit(
            "action.result",
            {
                "step": self._step,
                "action": name,
                "ok": result.ok,
                "detail": result.detail,
                "error": result.error,
            },
        )
        if result.observation is not None:
            await self.run.emit(
                "observation",
                {"step": self._step, "text": result.observation[:EVENT_OBSERVATION_LIMIT]},
            )
        if result.screenshot and self._screenshots and self.run.spec.store_screenshots:
            await self._screenshots.save(self.run.id, self._step, result.screenshot)
            await self.run.emit("screenshot.stored", {"step": self._step})

    def _should_look(self) -> bool:
        return self._vision_on and self._consecutive_failures >= self._stuck_threshold

    async def _reply(self, call: dict[str, Any], payload: dict[str, Any]) -> None:
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(payload, ensure_ascii=False),
            }
        )

    async def _attach_image(self, image: bytes) -> None:
        encoded = base64.b64encode(image).decode("ascii")
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Screenshot of the current viewport."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    },
                ],
            }
        )

    async def _handle_done(self, call: dict[str, Any], args: dict[str, Any]) -> bool:
        result = args.get("result")
        schema = self.run.spec.output_schema
        if schema is not None:
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    pass
            try:
                jsonschema.validate(result, schema)
            except jsonschema.ValidationError as exc:
                await self._reply(
                    call,
                    {
                        "ok": False,
                        "error": f"the result does not match the caller's schema: {exc.message}. "
                        "Fix it and call done() again.",
                    },
                )
                await self.run.emit(
                    "action.result",
                    {
                        "step": self._step,
                        "action": "done",
                        "ok": False,
                        "error": exc.message,
                        "detail": "",
                    },
                )
                return False
        await self._finish(Outcome.COMPLETED, result=result)
        return True

    # ---- confirmation ----------------------------------------------------

    async def _risk_gate(
        self, finding: RiskFinding, action: str, context: dict[str, Any]
    ) -> None:
        await self.run.emit(
            "risky_action.detected",
            {
                "step": self._step,
                "action": action,
                "source": str(finding.source),
                "purpose": finding.detail,
                **context,
            },
        )
        if self.run.spec.autonomy is not Autonomy.CONFIRM_RISKY:
            return

        loop = asyncio.get_running_loop()
        pending = PendingConfirmation(
            step=self._step,
            action=action,
            purpose=finding.detail,
            source=str(finding.source),
            snapshot=self.session.last_snapshot,
            future=loop.create_future(),
        )
        self.run.pending = pending
        self.run.record.status = str(RunStatus.AWAITING_CONFIRMATION)
        self.session.idle_suspended = True
        await self.run.persist()
        await self.run.emit("confirmation.requested", self.run.confirmation_view() or {})

        try:
            decision, note = await asyncio.wait_for(
                pending.future, timeout=self.run.spec.confirmation_timeout_seconds
            )
        except TimeoutError:
            decision, note = "timeout", None
        finally:
            self.run.pending = None
            self.session.idle_suspended = False
            self.run.record.status = str(RunStatus.RUNNING)
            await self.run.persist()

        await self.run.emit(
            "confirmation.resolved", {"step": self._step, "decision": decision, "note": note}
        )
        if decision == "approve":
            return
        if decision == "reject":
            raise ActionRejected(
                f"the caller rejected this action{f': {note}' if note else ''}. Find another way "
                "or give up."
            )
        if decision == "timeout":
            raise RunAborted("nobody answered the confirmation request in time")
        raise RunAborted("the caller aborted this run")

    # ---- finishing -------------------------------------------------------

    async def _finish(self, outcome: Outcome, *, result: Any = None, error: str | None = None) -> None:
        self.run.record.sources = list(self.session.visited_urls)
        await self.run.mark_finished(outcome, result=result, error=error)
        event = {
            Outcome.COMPLETED: "run.completed",
            Outcome.FAILED: "run.failed",
            Outcome.ABORTED: "run.aborted",
            Outcome.BLOCKED: "run.blocked",
        }[outcome]
        await self.run.emit(
            event,
            {
                "outcome": str(outcome),
                "result": result,
                "error": error,
                "sources": self.run.record.sources,
                "steps": self._step,
            },
        )


def _strip_images(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, list):
        return message
    text = " ".join(part.get("text", "") for part in content if part.get("type") == "text")
    return {**message, "content": text or "(screenshot omitted)"}
