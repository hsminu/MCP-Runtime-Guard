"""서버 등록 Registry(C, 개발문서 10.1·2.1, I10).

**지문(C4.12).** FP-01(2026-10-18·10-26 결정)대로 A transport가 SDK로 다시 만든 정의 dict
(`ToolListing.tools`, `model_dump(mode="json", by_alias=True, exclude_unset=True)`)에 B의
`tool_fingerprint`(RFC 8785 JCS, SHA-256)를 적용한 값이다. 등록과 연결 직후 대조(A
`live_fingerprints`)가 `listing_fingerprints` 하나를 쓰면, 한쪽만 계산이 바뀌어 정상 서버가
모두 PENDING이 되는 일이 없다. 목록이 미완료이거나, 도구 이름이 없거나 겹치거나, JCS 정규화가
실패하면 지문을 하나도 만들지 않는다(fail-closed).

**Registry(C4.10·C4.11·C4.18).** `ServerRegistry`는 B `RegistryStore`의 저장 사본을 들고
`ports.Registry` 동기 조회에 답한다. 제어 연산은 하나씩 직렬화하고, 저장이 성공하면 반환 사본으로
캐시를 곧바로 바꾼다(반환과 교체 사이 await 없음, `RegistryStore` 설명).

- 저장 호출이 사본을 돌려주지 않으면(실패, 기다리는 사이 취소, 예상 밖 예외) DB에 커밋됐는지
  알 수 없다(C4.19). 저장 쓰기를 writer 스레드로 옮기면 커밋 뒤 반환 전에 취소가 들어올 수 있다.
  그 서버의 캐시는 DB와 다를 수 있다고 표시하고, 표시된 서버의 ACTIVE는 PENDING으로 본다.
  그래서 `block`이 커밋된 뒤 취소돼도 ACTIVE가 남지 않는다. 그 서버의 다음 제어 연산이나
  `refresh`가 DB 행을 다시 읽어 표시를 푼다. 저절로 다시 읽지는 않는다.

- 등록 레코드는 설정 파일의 서버 레코드(경계·도구 종류·인자)와 실제 목록(스키마·지문)을 합친
  것이다(10.1). 목록에는 있지만 설정에 종류가 없는 도구는 `OTHER`로 등록한다. 정책·Catalog가
  `OTHER`를 노출하거나 실행하지 않으므로 지문 변경만 잡고 쓰이지 않는다. 설정에는 있지만 목록에
  없는 도구는 등록하지 않는다.
- DB의 ACTIVE만으로는 실행하지 않는다. 연결마다 `observe`가 실제 목록과 대조하고, A의
  `LiveCheckedRegistry`가 대조 전 지문을 None으로 막는다. 정의가 바뀌면 ACTIVE는 즉시 PENDING이
  되고 바뀐 도구의 대기 승인은 무효가 된다. 대조를 기록하지 못한 서버는 이번 실행 동안 PENDING으로
  본다.
- ACTIVE로 올리는 것(`activate`)은 사용자 확인 뒤 A의 정책 저장·적용 흐름이 부른다. 넘기는 지문은
  캐시의 등록 지문, 곧 사용자가 화면에서 본 지문이다.
- 사용자가 도구 종류·인자를 바꾸면(A155) `apply_settings`가 지문은 두고 그 값만 등록에 쓴다. 바뀐
  도구가 있으면 ACTIVE는 PENDING이 되어 다시 활성화해야 한다. 경계 변경은 여기서 다루지 않는다.

정의는 신뢰하지 않는 서버 데이터다. 오류에 서버 텍스트를 싣지 않고, 저장 실패는 B의 `StoreError`
(고정 사유)를 그대로 올린다. transport 패키지는 `services.product_assembly`만 가져오므로(I1, A
구조 테스트) 목록은 `ToolListing`을 가져오지 않고 같은 모양(`ToolListingView`)으로 받는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from guard_app.domain.contracts import (
    FingerprintChange,
    RegisteredServer,
    RegistryEntry,
    ScanReport,
)
from guard_app.domain.enums import RegStatus, ToolKind
from guard_app.domain.json_types import FrozenJsonObject
from guard_app.domain.models import ToolSpec
from guard_app.ports import RegistryStore
from guard_app.security.digest import JcsCanonicalizationError, tool_fingerprint
from guard_app.services.catalog import ServerSettings
from guard_app.services.catalog_sources import valid_server_id

InvalidateTool = Callable[[str, str], Awaitable[int]]


class RegistryError(RuntimeError):
    """서버 텍스트를 싣지 않는 고정 오류 코드."""


class ToolListingView(Protocol):
    """A transport `ToolListing`의 모양. 미완료 목록의 `tools`는 비어 있다."""

    @property
    def complete(self) -> bool: ...

    @property
    def tools(self) -> tuple[FrozenJsonObject, ...]: ...


def listing_fingerprints(listing: ToolListingView) -> dict[str, str]:
    """완료된 목록 하나의 도구 이름 → 지문. 순서는 목록 순서다.

    실패 코드: `REG_LISTING_INVALID`(모양이 다름), `REG_LISTING_INCOMPLETE`(미완료),
    `REG_TOOL_NAME_INVALID`(이름이 없거나 비었거나 문자열이 아님), `REG_TOOL_NAME_DUPLICATE`,
    `REG_FINGERPRINT_FAILED`(JCS로 나타낼 수 없는 값).
    """
    complete = getattr(listing, "complete", None)
    tools = getattr(listing, "tools", None)
    if (
        type(complete) is not bool
        or type(tools) is not tuple
        or any(type(definition) is not FrozenJsonObject for definition in tools)
    ):
        raise RegistryError("REG_LISTING_INVALID")
    if not complete:
        raise RegistryError("REG_LISTING_INCOMPLETE")
    fingerprints: dict[str, str] = {}
    for definition in tools:
        name = definition.get("name")
        if type(name) is not str or not name:
            raise RegistryError("REG_TOOL_NAME_INVALID")
        if name in fingerprints:
            raise RegistryError("REG_TOOL_NAME_DUPLICATE")
        try:
            fingerprints[name] = tool_fingerprint(definition.to_dict())
        except JcsCanonicalizationError:
            raise RegistryError("REG_FINGERPRINT_FAILED") from None
    return fingerprints


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ServerRegistry:
    """B `RegistryStore` 위의 C Registry. 동기 조회는 캐시, 제어는 직렬화한 저장 뒤 캐시 교체다."""

    def __init__(self, store: RegistryStore, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._store = store
        self._clock = clock
        self._entries: dict[str, RegistryEntry] = {}
        # 연결 직후 대조를 기록하지 못한 서버. 이번 실행 동안 PENDING으로 본다.
        self._unrecorded: set[str] = set()
        # 제어 쓰기의 결과를 모르는 서버. DB 행을 다시 읽을 때까지 PENDING으로 본다.
        self._stale: set[str] = set()
        self._lock = asyncio.Lock()

    # ports.Registry: 동기 조회

    def registration(self, server_id: str) -> RegisteredServer | None:
        entry = self._entries.get(server_id)
        if entry is None:
            return None
        registration = entry.registration
        if registration.status is RegStatus.ACTIVE and (
            server_id in self._unrecorded or server_id in self._stale
        ):
            return registration.model_copy(update={"status": RegStatus.PENDING})
        return registration

    def status(self, server_id: str) -> RegStatus | None:
        registration = self.registration(server_id)
        return None if registration is None else registration.status

    def fingerprint(self, tool: ToolSpec) -> str | None:
        """등록된 지문. 같은 서버의 같은 이름 도구가 없으면 None이다."""
        registration = self.registration(tool.server_id)
        if registration is None:
            return None
        found = next((t for t in registration.tools if t.tool_name == tool.tool_name), None)
        return None if found is None else found.fingerprint

    def entry(self, server_id: str) -> RegistryEntry | None:
        """화면용 저장 사본(최근 검사·revision). 실행 허가가 아니다.

        제어 연산이 실패한 뒤에는 DB와 다를 수 있다. 화면은 `refresh`로 다시 읽은 사본을 쓴다.
        """
        return self._entries.get(server_id)

    # 제어: 하나씩, 저장 성공 뒤 반환 사본으로 캐시 교체

    async def load(self) -> None:
        """앱 시작 때 저장된 등록을 캐시로 복원한다. 대조 실패·결과 불명 표시는 지운다."""
        async with self._lock:
            entries = await self._store.list_registrations()
            self._entries = {entry.registration.server_id: entry for entry in entries}
            self._unrecorded.clear()
            self._stale.clear()

    async def refresh(self, server_id: str) -> RegistryEntry | None:
        """DB의 현재 행으로 캐시를 맞추고 결과 불명 표시를 푼다. 행이 없으면 None이다.

        대조를 기록하지 못한 표시는 풀지 않는다. 그 서버는 다음 시작에서 다시 대조한다.
        """
        async with self._lock:
            await self._reread(server_id)
            return self._entries.get(server_id)

    async def register(self, server: ServerSettings, listing: ToolListingView) -> RegistryEntry:
        """신규 서버를 PENDING으로 등록한다(C4.11). 이미 있으면 `REG_SERVER_EXISTS`다."""
        async with self._lock:
            _check_settings(server)
            await self._reread_if_stale(server.id)
            if server.id in self._entries:
                raise RegistryError("REG_SERVER_EXISTS")
            try:
                registration = RegisteredServer(
                    server_id=server.id,
                    status=RegStatus.PENDING,
                    boundary=server.as_boundary(),
                    tools=_tool_specs(server, listing),
                )
            except ValidationError:
                raise RegistryError("REG_REGISTRATION_INVALID") from None
            return await self._write(
                server.id, self._store.register_server(registration, at_utc=self._clock())
            )

    async def observe(
        self,
        server: ServerSettings,
        listing: ToolListingView,
        *,
        invalidate_tool: InvalidateTool | None = None,
    ) -> FingerprintChange:
        """연결 직후 실제 목록을 등록과 대조한다(I10).

        바뀌었으면 `record_fingerprint_change`가 ACTIVE를 PENDING으로 내린 사본으로 캐시를 바꾸고,
        바뀐 도구마다 `invalidate_tool`(Guard의 승인 무효화)을 부른다. Guard가 아직 없는 앱 시작
        직후에는 대기 승인도 없으므로 None으로 둔다. 목록이 미완료·형식 오류면 쓰지 않고
        `RegistryError`다(A의 대조가 그 서버를 이번 실행 동안 막는다). 기록 호출이 결과를 돌려주지
        않으면(실패·취소·예상 밖 예외) 그 서버를 이번 실행 동안 PENDING으로 보고 오류를 그대로
        올린다.

        무효화 도중 취소되면 남은 도구는 무효화하지 않는다. 캐시는 이미 ACTIVE가 아니고 지문도
        바뀌어서, 그 도구의 대기 승인은 재판정에서 거절된다.
        """
        async with self._lock:
            _check_settings(server)
            await self._reread_if_stale(server.id)
            current = self._entries.get(server.id)
            if current is None:
                raise RegistryError("REG_SERVER_UNKNOWN")
            if server.as_boundary() != current.registration.boundary:
                raise RegistryError("REG_SETTINGS_MISMATCH")
            tools = _tool_specs(server, listing)
            recorded = False
            try:
                change = await self._store.record_fingerprint_change(
                    server.id, tools, at_utc=self._clock()
                )
                recorded = True
            finally:
                if not recorded:
                    self._unrecorded.add(server.id)
            if change.changed_tools:
                self._entries[server.id] = change.entry
                if invalidate_tool is not None:
                    for name in change.changed_tools:
                        await invalidate_tool(server.id, name)
            return change

    async def apply_settings(
        self, server: ServerSettings, *, expected_row_revision: int
    ) -> FingerprintChange:
        """설정의 도구 종류·인자를 등록에 반영한다(B6.20 `record_settings_change`, #86).

        등록 도구마다 스키마·지문은 그대로 두고 설정의 `kind`·`destination_arg`·`resource_arg`만
        얹는다. 설정에 없는 도구는 등록 때와 같이 `OTHER`다. 바뀐 도구가 있으면 B가 그 도구의 검토
        설명을 비우고 ACTIVE를 PENDING으로 내린다. 경계(`tier`·`max_label`)가 다르면 쓰지 않고
        `REG_SETTINGS_MISMATCH`다. 기록 호출이 결과를 돌려주지 않으면 그 서버를 다시 읽게 표시하고
        오류를 그대로 올린다(C4.19).
        """
        async with self._lock:
            _check_settings(server)
            await self._reread_if_stale(server.id)
            current = self._entries.get(server.id)
            if current is None:
                raise RegistryError("REG_SERVER_UNKNOWN")
            if server.as_boundary() != current.registration.boundary:
                raise RegistryError("REG_SETTINGS_MISMATCH")
            configured = {tool.name: tool for tool in server.tools}
            tools: list[ToolSpec] = []
            for tool in current.registration.tools:
                setting = configured.get(tool.tool_name)
                try:
                    tools.append(
                        ToolSpec(
                            server_id=tool.server_id,
                            tool_name=tool.tool_name,
                            kind=ToolKind.OTHER if setting is None else setting.kind,
                            input_schema=tool.input_schema,
                            destination_arg=None if setting is None else setting.destination_arg,
                            resource_arg=None if setting is None else setting.resource_arg,
                            fingerprint=tool.fingerprint,
                            reviewed_description=tool.reviewed_description,
                        )
                    )
                except ValidationError:
                    raise RegistryError("REG_TOOL_INVALID") from None
            written = False
            try:
                change = await self._store.record_settings_change(
                    server.id,
                    tuple(tools),
                    expected_row_revision=expected_row_revision,
                    at_utc=self._clock(),
                )
                written = True
            finally:
                if not written:
                    self._stale.add(server.id)
            self._entries[server.id] = change.entry
            return change

    async def activate(
        self,
        server_id: str,
        *,
        expected_row_revision: int,
        reviewed_by: str,
        reviewed_scan_id: str | None = None,
    ) -> RegistryEntry:
        """사용자 확인 뒤 PENDING을 ACTIVE로 올린다(C4.11).

        넘기는 지문은 캐시의 등록 지문(사용자가 본 것)이다. 이번 실행에서 대조를 기록하지 못한
        서버는 `REG_SERVER_UNRECORDED`로 올리지 않는다.
        """
        async with self._lock:
            await self._reread_if_stale(server_id)
            current = self._entries.get(server_id)
            if current is None:
                raise RegistryError("REG_SERVER_UNKNOWN")
            if server_id in self._unrecorded:
                raise RegistryError("REG_SERVER_UNRECORDED")
            return await self._write(
                server_id,
                self._store.activate_server(
                    server_id,
                    expected_row_revision=expected_row_revision,
                    tool_fingerprints={
                        tool.tool_name: tool.fingerprint for tool in current.registration.tools
                    },
                    reviewed_by=reviewed_by,
                    at_utc=self._clock(),
                    reviewed_scan_id=reviewed_scan_id,
                ),
            )

    async def record_scan(self, scan_id: str, report: ScanReport) -> RegistryEntry:
        """검사 보고서를 저장하고 그 서버의 `last_scan_id`를 캐시에 반영한다(C4.9).

        검사는 상태를 바꾸지 않는다. 사용자가 이 보고서를 보고 확인하면 A가 `activate`에
        `reviewed_scan_id=scan_id`로 넘긴다. 저장소가 서버와 지문 집합을 등록과 대조한다.
        """
        if type(report) is not ScanReport:
            raise RegistryError("REG_SCAN_INVALID")
        async with self._lock:
            await self._reread_if_stale(report.server_id)
            if report.server_id not in self._entries:
                raise RegistryError("REG_SERVER_UNKNOWN")
            return await self._write(
                report.server_id,
                self._store.record_scan_report(scan_id, report, at_utc=self._clock()),
            )

    async def block(self, server_id: str, *, expected_row_revision: int) -> RegistryEntry:
        async with self._lock:
            await self._reread_if_stale(server_id)
            return await self._write(
                server_id,
                self._store.block_server(
                    server_id, expected_row_revision=expected_row_revision, at_utc=self._clock()
                ),
            )

    async def request_review(self, server_id: str, *, expected_row_revision: int) -> RegistryEntry:
        async with self._lock:
            await self._reread_if_stale(server_id)
            return await self._write(
                server_id,
                self._store.request_review(
                    server_id, expected_row_revision=expected_row_revision, at_utc=self._clock()
                ),
            )

    async def save_reviewed_descriptions(
        self,
        server_id: str,
        descriptions: Mapping[str, str],
        *,
        expected_row_revision: int,
    ) -> RegistryEntry:
        async with self._lock:
            await self._reread_if_stale(server_id)
            return await self._write(
                server_id,
                self._store.save_reviewed_descriptions(
                    server_id,
                    descriptions,
                    expected_row_revision=expected_row_revision,
                    at_utc=self._clock(),
                ),
            )

    # 잠근 상태에서만 부른다.

    async def _write(self, server_id: str, write: Awaitable[RegistryEntry]) -> RegistryEntry:
        # 예외를 잡지 않고 finally로 표시한다. 저장 호출이 사본을 돌려주지 않았다면 실패·취소·
        # 예상 밖 예외 모두 커밋됐는지 모른다(#53 B 리뷰).
        written = False
        try:
            entry = await write
            written = True
        finally:
            if not written:
                self._stale.add(server_id)
        self._entries[server_id] = entry
        return entry

    async def _reread_if_stale(self, server_id: str) -> None:
        if server_id in self._stale:
            await self._reread(server_id)

    async def _reread(self, server_id: str) -> None:
        entry = await self._store.load_registration(server_id)
        if entry is None:
            self._entries.pop(server_id, None)
        else:
            self._entries[server_id] = entry
        self._stale.discard(server_id)


def _check_settings(server: object) -> None:
    # ServerSettings도 같은 검사를 하지만 등록의 마지막 방어로 B 함수를 다시 쓴다(C4.18).
    if type(server) is not ServerSettings:
        raise RegistryError("REG_SETTINGS_INVALID")
    if not valid_server_id(server.id):
        raise RegistryError("REG_SERVER_ID_INVALID")


def _tool_specs(server: ServerSettings, listing: ToolListingView) -> tuple[ToolSpec, ...]:
    """목록의 모든 도구를 등록 도구로 바꾼다. 종류·인자는 설정, 스키마·지문은 목록이다."""
    # listing_fingerprints는 목록 순서대로 이름을 확인해 담으므로 둘을 나란히 읽는다.
    fingerprints = listing_fingerprints(listing)
    configured = {tool.name: tool for tool in server.tools}
    specs: list[ToolSpec] = []
    for definition, (name, fingerprint) in zip(listing.tools, fingerprints.items(), strict=True):
        schema = definition.get("inputSchema")
        if type(schema) is not FrozenJsonObject:
            raise RegistryError("REG_TOOL_SCHEMA_INVALID")
        settings = configured.get(name)
        try:
            specs.append(
                ToolSpec(
                    server_id=server.id,
                    tool_name=name,
                    kind=ToolKind.OTHER if settings is None else settings.kind,
                    input_schema=schema,
                    destination_arg=None if settings is None else settings.destination_arg,
                    resource_arg=None if settings is None else settings.resource_arg,
                    fingerprint=fingerprint,
                )
            )
        except ValidationError:
            raise RegistryError("REG_TOOL_INVALID") from None
    return tuple(specs)
