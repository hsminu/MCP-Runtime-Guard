"""6장: 격리된 MCP 결과를 검사·분류·커밋한 뒤에만 공개한다(I4/I5/I6)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from guard_app.domain.contracts import (
    OutcomeUnknown,
    RawResult,
    Released,
    ReleasedResult,
    Withheld,
)
from guard_app.domain.enums import Label, RequestStatus, TaskStatus, ToolKind
from guard_app.domain.errors import StoreError
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ResolvedRequest, TaskState
from guard_app.domain.reasons import Reason
from guard_app.ports import Store
from guard_app.services.catalog import Catalog
from guard_app.services.state_checks import checked_task, preserve_task_context


@dataclass(frozen=True, slots=True, repr=False)
class _CheckedResult:
    text: str
    structured: FrozenJsonObject | None
    is_error: bool

    @property
    def has_data(self) -> bool:
        """빈 structured object도 수신된 자료로 다룬다. 텍스트만 0 bytes면 무자료다."""
        return bool(self.text) or self.structured is not None


class ResponseGate:
    """RawResult를 모델·UI·로그에 노출하지 않고 Store 커밋 성공 후 공개한다.

    READ 결과의 label은 Catalog만 결정한다. SEND 결과는 READ 등급 병합을
    호출하지 않는다. 커밋 실패 후의 격리 기록도 실패하면 global halt를 세우고
    Guard의 after_dispatch 경계에 전달한다. 실제 저장소 원자성은 C 계약이다.
    """

    def __init__(
        self,
        store: Store,
        catalog: Catalog,
        *,
        utc_now: Callable[[], datetime],
        global_halt: Callable[[], None],
        max_result_bytes: int = 262144,
    ) -> None:
        if type(max_result_bytes) is not int or max_result_bytes < 1:
            raise ValueError("결과 크기 상한은 양의 정수여야 한다")
        if not callable(utc_now) or not callable(global_halt):
            raise ValueError("응답 게이트의 시계·정지 함수가 필요하다")
        self._store = store
        self._catalog = catalog
        self._utc_now = utc_now
        self._global_halt = global_halt
        self._max_result_bytes = max_result_bytes

    async def handle(
        self,
        req: ResolvedRequest,
        raw: RawResult,
        *,
        expected_state: TaskState | None = None,
    ) -> Released | Withheld | OutcomeUnknown:
        """예약된 요청 전용. Guard는 실행 직전의 신뢰 Task 상태도 전달한다.

        Store의 반환 상태를 검사한 뒤 공개한다. 직접 단위 호출의 expected_state
        생략은 비교 기준을 생략할 뿐 요청 식별자와 분류 결과 검증은 생략하지 않는다.
        unexpected 오류·취소는 after_dispatch로 전달한다.
        """
        expected = None
        try:
            try:
                raw = RawResult.model_validate(raw)
            except ValidationError:
                raise StoreError(Reason.STATE_COMMIT_FAILED) from None
            if expected_state is not None:
                expected = self._validated_state(req, expected_state)
            return await self._handle(req, raw, expected_state=expected)
        except StoreError as error:
            try:
                return await self._failure(
                    req,
                    Reason.STATE_COMMIT_FAILED,
                    exception_type=type(error).__name__,
                    expected_state=expected,
                )
            except StoreError:
                self._global_halt()
                raise

    async def _handle(
        self,
        req: ResolvedRequest,
        raw: RawResult,
        *,
        expected_state: TaskState | None,
    ) -> Released | Withheld | OutcomeUnknown:
        if req.tool.kind not in (ToolKind.READ, ToolKind.SEND):
            return await self._failure(req, Reason.TOOL_UNSUPPORTED, expected_state=expected_state)
        if raw.failed:
            reason = raw.failure_reason or Reason.POST_DISPATCH_ERROR
            return await self._failure(
                req, reason, no_data_read=True, expected_state=expected_state
            )

        checked = self._check_shape(raw.payload)
        if checked is None:
            return await self._failure(
                req, Reason.UNSUPPORTED_CONTENT, expected_state=expected_state
            )
        if req.tool.kind is ToolKind.SEND:
            if checked.is_error:
                return await self._failure(
                    req, Reason.POST_DISPATCH_ERROR, expected_state=expected_state
                )
            await self._store.commit_send_outcome(req, at_utc=self._utc_now())
            label = None
        else:
            if checked.is_error and not checked.has_data:
                return await self._failure(
                    req,
                    Reason.POST_DISPATCH_ERROR,
                    no_data_read=True,
                    expected_state=expected_state,
                )
            actual = None if checked.structured is None else checked.structured.get("resource_id")
            # Resource ID의 타입·정확한 일치도 Catalog의 신뢰 설정 해석에 맡긴다.
            label = self._catalog.classify_read(req, actual if type(actual) is str else None)
            committed = await self._store.commit_observation(req, label, at_utc=self._utc_now())
            self._check_observation(req, committed, label, expected_state=expected_state)
            if label is None:
                return Withheld(request_id=req.request_id, reasons=(Reason.RESULT_UNCLASSIFIED,))

        return Released(
            request_id=req.request_id,
            result=ReleasedResult(
                text=checked.text,
                structured_content=checked.structured,
                is_error=checked.is_error,
            ),
            label=label,
        )

    def _check_shape(self, payload: FrozenJsonObject | None) -> _CheckedResult | None:
        if payload is None:
            return None
        content = payload.get("content")
        structured = payload.get("structuredContent")
        is_error = payload.get("isError", False)
        if (
            not isinstance(content, tuple)
            or (structured is not None and not isinstance(structured, FrozenJsonObject))
            or type(is_error) is not bool
        ):
            return None
        texts: list[str] = []
        byte_count = 0
        for block in content:
            if not isinstance(block, FrozenJsonObject) or block.get("type") != "text":
                return None
            text = block.get("text")
            if type(text) is not str:
                return None
            byte_count += len(text.encode("utf-8"))
            if byte_count > self._max_result_bytes:
                return None
            texts.append(text)
        if structured is not None:
            byte_count += len(
                json.dumps(
                    structured.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        if byte_count > self._max_result_bytes:
            return None
        return _CheckedResult("".join(texts), structured, is_error)

    async def _failure(
        self,
        req: ResolvedRequest,
        reason: Reason,
        *,
        no_data_read: bool = False,
        exception_type: str | None = None,
        expected_state: TaskState | None = None,
    ) -> Withheld | OutcomeUnknown:
        if req.tool.kind is ToolKind.SEND:
            committed = await self._store.commit_request_failure(
                req,
                request_status=RequestStatus.UNKNOWN_OUTCOME,
                task_status=TaskStatus.UNKNOWN_OUTCOME,
                reason=reason,
                at_utc=self._utc_now(),
                exception_type=exception_type,
            )
            state = self._validated_state(req, committed)
            self._preserve_context(state, expected_state, allow_last_read_change=False)
            if state.status is not TaskStatus.UNKNOWN_OUTCOME:
                raise StoreError(Reason.STATE_COMMIT_FAILED)
            return OutcomeUnknown(request_id=req.request_id, reasons=(reason,))
        committed = await self._store.commit_request_failure(
            req,
            request_status=RequestStatus.FAILED if no_data_read else RequestStatus.WITHHELD,
            task_status=TaskStatus.RUNNING if no_data_read else TaskStatus.HALTED,
            reason=reason,
            at_utc=self._utc_now(),
            exception_type=exception_type,
        )
        state = self._validated_state(req, committed)
        self._preserve_context(state, expected_state, allow_last_read_change=not no_data_read)
        if state.status is not (TaskStatus.RUNNING if no_data_read else TaskStatus.HALTED):
            raise StoreError(Reason.STATE_COMMIT_FAILED)
        return Withheld(request_id=req.request_id, reasons=(reason,))

    @staticmethod
    def _validated_state(req: ResolvedRequest, value: TaskState) -> TaskState:
        return checked_task(value, task_id=req.task_id, epoch=req.epoch)

    @staticmethod
    def _preserve_context(
        state: TaskState, expected: TaskState | None, *, allow_last_read_change: bool = True
    ) -> None:
        preserve_task_context(state, expected, allow_last_read_change=allow_last_read_change)

    def _check_observation(
        self,
        req: ResolvedRequest,
        value: TaskState,
        label: Label | None,
        *,
        expected_state: TaskState | None,
    ) -> None:
        state = self._validated_state(req, value)
        self._preserve_context(state, expected_state, allow_last_read_change=label is not None)
        if label is None:
            valid = state.status is TaskStatus.HALTED and state.unclassified
        else:
            valid = (
                state.status is TaskStatus.RUNNING
                and not state.unclassified
                and state.high_watermark >= label
                and state.last_read_label is label
            )
        if not valid:
            raise StoreError(Reason.STATE_COMMIT_FAILED)
