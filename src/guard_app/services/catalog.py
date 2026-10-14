"""기술설계서 4장: 모델 제안을 신뢰 등록·Profile로 해석한다."""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType

from pydantic import ValidationError

from guard_app.domain.contracts import RegisteredServer, ToolView
from guard_app.domain.enums import Label, RegStatus, ToolKind
from guard_app.domain.errors import ResolutionError
from guard_app.domain.models import Boundary, ProposedCall, ResolvedRequest, TaskState, ToolSpec
from guard_app.domain.reasons import Reason
from guard_app.ports import Registry
from guard_app.services.catalog_schema import ArgumentSchema, CatalogConfigError
from guard_app.services.catalog_sources import (
    SourceClassifier,
    SourceConfigError,
    SourceRule,
    canonical_resource_parts,
    valid_server_id,
)

__all__ = ["Catalog", "CatalogConfigError", "ResourceResolver"]

type ResourceResolver = Callable[[ToolSpec, str], str]


class Catalog:
    """운영자 Profile의 불변 규칙과 현재 Registry 조회를 결합한다.

    ResourceResolver는 C safe_path에서 검증한 root-relative POSIX ID를 반환한다.
    미연결 시 자원형 READ는 차단한다. Callback의 알려진 거부는 원문 없는
    ResolutionError로 번역해 전달해야 하며, 예상 밖 오류는 Guard의 예약 전
    fail_closed 경계로 전파한다. 이 서비스는 파일·URL에 접근하지 않는다.
    """

    def __init__(
        self,
        registry: Registry,
        *,
        profile_id: str,
        destinations: tuple[Boundary, ...] = (),
        source_rules: tuple[SourceRule, ...] = (),
        resource_resolver: ResourceResolver | None = None,
    ) -> None:
        if type(profile_id) is not str or not profile_id:
            raise CatalogConfigError("Catalog Profile ID가 유효하지 않다")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in profile_id):
            raise CatalogConfigError("Catalog Profile ID는 UTF-8 문자열이어야 한다")
        if not isinstance(destinations, tuple) or any(
            not isinstance(boundary, Boundary) for boundary in destinations
        ):
            raise CatalogConfigError("목적지는 검증된 Boundary의 tuple이어야 한다")
        if resource_resolver is not None and not callable(resource_resolver):
            raise CatalogConfigError("자원 해석기는 호출 가능한 함수여야 한다")
        try:
            boundaries = tuple(Boundary.model_validate(boundary) for boundary in destinations)
            sources = SourceClassifier(source_rules)
        except (ValidationError, SourceConfigError):
            raise CatalogConfigError("Catalog의 목적지·출처 설정이 유효하지 않다") from None
        destination_map = {boundary.boundary_id: boundary for boundary in boundaries}
        if len(destination_map) != len(boundaries):
            raise CatalogConfigError("목적지 ID는 중복될 수 없다")
        self._registry = registry
        self._profile_id = profile_id
        self._destinations = MappingProxyType(destination_map)
        self._sources = sources
        self._resource_resolver = resource_resolver

    def resolve_tool(self, tool_ref: str) -> ToolSpec:
        """첫 구분자 뒤의 도구 이름을 그대로 보존하고 현재 등록을 확인한다."""
        return self._find_tool(tool_ref)[1]

    def resolve(
        self,
        state: TaskState,
        call: ProposedCall,
        *,
        request_id: str,
        call_seq: int,
    ) -> ResolvedRequest:
        """Guard의 ID·순서와 Task의 신뢰 식별자로 요청을 만든다. ID를 생성하지 않는다."""
        if state.profile_id != self._profile_id:
            raise ResolutionError(Reason.POLICY_CHANGED)
        registration, tool = self._find_tool(call.tool_ref)
        ArgumentSchema(tool.input_schema).validate(call.arguments)
        destination = self._resolve_destination(tool, call)
        resource_id = self._resolve_resource(tool, call)
        return ResolvedRequest(
            request_id=request_id,
            task_id=state.task_id,
            epoch=state.epoch,
            call_seq=call_seq,
            tool=tool,
            arguments=call.arguments,
            resource_id=resource_id,
            server_boundary=registration.boundary,
            destination=destination,
        )

    def classify_source(self, server_id: str, tool_name: str, resource_id: str) -> Label | None:
        """등록된 출처 규칙만 적용한다. 결과 본문이 주장하는 label은 받지 않는다."""
        return self._sources.classify(server_id, tool_name, resource_id)

    def classify_read(
        self, req: ResolvedRequest, actual_resource_id: str | None = None
    ) -> Label | None:
        """자원형은 기대 ID와 정확히 대조하고, 도구 단위형은 빈 경로만 분류한다."""
        if req.tool.kind is not ToolKind.READ:
            return None
        if req.tool.resource_arg is None:
            resource_id = ""
        else:
            if (
                req.resource_id is None
                or type(actual_resource_id) is not str
                or actual_resource_id != req.resource_id
            ):
                return None
            resource_id = req.resource_id
        return self.classify_source(req.tool.server_id, req.tool.tool_name, resource_id)

    def tool_views(self, server_ids: tuple[str, ...]) -> tuple[ToolView, ...]:
        """현재 ACTIVE·지원 종류·지문 일치 도구의 닫힌 스키마를 모델에 노출한다."""
        if not isinstance(server_ids, tuple):
            raise CatalogConfigError("도구 목록의 서버 ID는 tuple이어야 한다")
        seen: set[str] = set()
        views: list[ToolView] = []
        for server_id in server_ids:
            registration = self._registration(server_id)
            if server_id in seen:
                raise CatalogConfigError("도구 목록의 서버 ID는 중복될 수 없다")
            seen.add(server_id)
            if registration is None or registration.status is not RegStatus.ACTIVE:
                continue
            if self._registry.status(server_id) is not RegStatus.ACTIVE:
                continue
            for tool in registration.tools:
                if tool.kind is ToolKind.OTHER:
                    continue
                if self._registry.fingerprint(tool) != tool.fingerprint:
                    continue
                views.append(
                    ToolView(
                        tool_ref=f"{server_id}__{tool.tool_name}",
                        input_schema=ArgumentSchema(tool.input_schema).schema,
                        description="",
                    )
                )
        return tuple(views)

    def _registration(self, server_id: str) -> RegisteredServer | None:
        if not valid_server_id(server_id):
            raise CatalogConfigError("서버 ID가 네임스페이스 기준을 만족하지 않는다")
        registration = self._registry.registration(server_id)
        if registration is None:
            return None
        try:
            registration = RegisteredServer.model_validate(registration)
        except ValidationError:
            raise CatalogConfigError("Registry 등록 모델이 유효하지 않다") from None
        if registration.server_id != server_id:
            raise CatalogConfigError("Registry 조회 ID와 등록 서버 ID가 다르다")
        return registration

    def _find_tool(self, tool_ref: str) -> tuple[RegisteredServer, ToolSpec]:
        if type(tool_ref) is not str:
            raise ResolutionError(Reason.TOOL_UNSUPPORTED)
        server_id, separator, tool_name = tool_ref.partition("__")
        if not separator or not valid_server_id(server_id) or not tool_name:
            raise ResolutionError(Reason.TOOL_UNSUPPORTED)
        registration = self._registration(server_id)
        if registration is None:
            raise ResolutionError(Reason.UNKNOWN_SERVER)
        if (
            registration.status is not RegStatus.ACTIVE
            or self._registry.status(server_id) is not RegStatus.ACTIVE
        ):
            raise ResolutionError(Reason.REG_NOT_ACTIVE)
        tool = next((tool for tool in registration.tools if tool.tool_name == tool_name), None)
        if tool is None or tool.kind is ToolKind.OTHER:
            raise ResolutionError(Reason.TOOL_UNSUPPORTED)
        if self._registry.fingerprint(tool) != tool.fingerprint:
            raise ResolutionError(Reason.TOOL_FINGERPRINT_CHANGED)
        return registration, tool

    def _resolve_destination(self, tool: ToolSpec, call: ProposedCall) -> Boundary | None:
        if tool.kind is not ToolKind.SEND:
            return None
        if tool.destination_arg is None:
            raise ResolutionError(Reason.UNKNOWN_DESTINATION)
        destination_id = call.arguments.get(tool.destination_arg)
        if type(destination_id) is not str:
            raise ResolutionError(Reason.UNKNOWN_DESTINATION)
        destination = self._destinations.get(destination_id)
        if destination is None:
            raise ResolutionError(Reason.UNKNOWN_DESTINATION)
        return destination

    def _resolve_resource(self, tool: ToolSpec, call: ProposedCall) -> str | None:
        if tool.kind is not ToolKind.READ or tool.resource_arg is None:
            return None
        path = call.arguments.get(tool.resource_arg)
        if type(path) is not str or not path:
            raise ResolutionError(Reason.INVALID_ARGUMENTS)
        if self._resource_resolver is None:
            raise ResolutionError(Reason.INTERNAL_ERROR)
        resource_id = self._resource_resolver(tool, path)
        if not canonical_resource_parts(resource_id):
            raise ResolutionError(Reason.INVALID_ARGUMENTS)
        return resource_id
