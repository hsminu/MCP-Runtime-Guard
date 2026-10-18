"""기술설계서 4장: 모델 제안을 신뢰 등록·Profile로 해석한다."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from guard_app.core.access_rules import AccessRules, normalize_access_rules
from guard_app.domain.contracts import RegisteredServer, ToolView
from guard_app.domain.enums import Label, RegStatus, ToolKind, TrustTier
from guard_app.domain.errors import ResolutionError
from guard_app.domain.models import (
    Boundary,
    Frozen,
    NonEmptyString,
    ProposedCall,
    ResolvedRequest,
    TaskState,
    ToolSpec,
)
from guard_app.domain.reasons import Reason
from guard_app.ports import Registry
from guard_app.security.digest import JcsCanonicalizationError, digest, jcs_canonicalize
from guard_app.services.catalog_schema import ArgumentSchema, CatalogConfigError
from guard_app.services.catalog_sources import (
    SourceClassifier,
    SourceConfigError,
    SourceRule,
    canonical_resource_parts,
    valid_server_id,
)

__all__ = [
    "Catalog",
    "CatalogConfigError",
    "GuardSettings",
    "LoadedProfile",
    "Profile",
    "ResourceResolver",
    "ServerSettings",
    "ToolSettings",
    "load_profile_settings",
]

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


class _Settings(Frozen):
    """불변 설정의 repr/str에는 실행 인자·URL·입력 원문을 싣지 않는다."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


class _BoundarySettings(_Settings):
    id: NonEmptyString
    tier: TrustTier
    max_label: Label

    @field_validator("max_label", mode="before")
    @classmethod
    def label_name(cls, value: object) -> Label:
        if type(value) is Label:
            return value
        if type(value) is not str or value not in Label.__members__:
            raise ValueError("등급 이름이 유효하지 않다")
        return Label[value]

    @model_validator(mode="after")
    def check_boundary(self) -> Self:
        self.as_boundary()
        return self

    def as_boundary(self) -> Boundary:
        return Boundary(boundary_id=self.id, tier=self.tier, max_label=self.max_label)


class ToolSettings(_Settings):
    """10.1의 운영자 지정 도구 메타데이터. 스키마·지문·등록 상태는 C 소유다."""

    name: NonEmptyString
    kind: ToolKind
    resource_arg: NonEmptyString | None = None
    destination_arg: NonEmptyString | None = None

    @model_validator(mode="after")
    def check_arguments(self) -> Self:
        if (
            (self.kind is ToolKind.READ and self.destination_arg is not None)
            or (
                self.kind is ToolKind.SEND
                and (self.destination_arg is None or self.resource_arg is not None)
            )
            or (
                self.kind is ToolKind.OTHER
                and (self.resource_arg is not None or self.destination_arg is not None)
            )
        ):
            raise ValueError("도구 종류와 인자 설정이 맞지 않는다")
        # SourceRule과 같은 도구 이름 기준을 재사용한다.
        SourceRule(server_id="validation", tool_name=self.name, prefix=(), label=Label.PUBLIC)
        return self


class ServerSettings(_BoundarySettings):
    """C의 경로·실행 파일 검증에 넘길 값. 로더는 실행하거나 접근하지 않는다."""

    transport: Literal["stdio", "streamable_http"]
    command: NonEmptyString | None = None
    args: tuple[str, ...] = ()
    env_allow: tuple[NonEmptyString, ...] = ()
    url: NonEmptyString | None = None
    tools: tuple[ToolSettings, ...]

    @field_validator("id")
    @classmethod
    def server_name(cls, value: str) -> str:
        if not valid_server_id(value):
            raise ValueError("서버 ID가 유효하지 않다")
        return value

    @model_validator(mode="after")
    def check_server(self) -> Self:
        if self.transport == "stdio":
            if self.command is None or self.url is not None:
                raise ValueError("stdio 연결 설정이 유효하지 않다")
        elif self.url is None or self.command is not None or self.args or self.env_allow:
            raise ValueError("HTTP 연결 설정이 유효하지 않다")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("도구 이름은 서버 안에서 중복될 수 없다")
        return self


class _SourceSettings(_Settings):
    server: NonEmptyString
    tool: NonEmptyString
    prefix: tuple[str, ...]
    label: Label

    @field_validator("label", mode="before")
    @classmethod
    def label_name(cls, value: object) -> Label:
        return _BoundarySettings.label_name(value)

    @model_validator(mode="after")
    def check_source(self) -> Self:
        self.as_rule()
        return self

    def as_rule(self) -> SourceRule:
        return SourceRule(
            server_id=self.server, tool_name=self.tool, prefix=self.prefix, label=self.label
        )


class _ProfileSettings(_Settings):
    id: NonEmptyString
    servers: tuple[NonEmptyString, ...]
    destinations: tuple[NonEmptyString, ...] = ()
    sources: tuple[_SourceSettings, ...] = ()


class _PolicySettings(_Settings):
    taint_mode: Literal["accumulate", "last_read", "none"] = "accumulate"
    approval_ttl_seconds: int = Field(default=300, gt=0)


class _AccessSettings(_Settings):
    allow: tuple[tuple[NonEmptyString, NonEmptyString], ...] = ()
    deny: tuple[tuple[NonEmptyString, NonEmptyString], ...] = ()


class _Configuration(_Settings):
    profile: _ProfileSettings
    servers: tuple[ServerSettings, ...]
    destinations: tuple[_BoundarySettings, ...] = ()
    policy: _PolicySettings = Field(default_factory=_PolicySettings)
    access_rules: _AccessSettings = Field(default_factory=_AccessSettings)


@dataclass(frozen=True, slots=True, repr=False)
class Profile:
    """선택된 Profile의 불변 값. 파일 형식 계약은 아니다."""

    profile_id: str
    server_ids: tuple[str, ...]
    destination_ids: tuple[str, ...]
    source_rules: tuple[SourceRule, ...]

    def __repr__(self) -> str:
        return "Profile(<redacted>)"


class GuardSettings(_PolicySettings):
    """GuardService에 전달할 Profile·버전·정책 값. 실행 의존성은 별도로 연결한다."""

    profile_id: NonEmptyString
    policy_version: NonEmptyString


@dataclass(frozen=True, slots=True, repr=False)
class LoadedProfile:
    """검증된 독립 사본. C 검증과 A 시작 연결은 별도로 필요하다."""

    profile: Profile
    catalog: Catalog
    access_rules: AccessRules
    guard_settings: GuardSettings
    servers: tuple[ServerSettings, ...]

    @property
    def policy_version(self) -> str:
        return self.guard_settings.policy_version

    def __repr__(self) -> str:
        return "LoadedProfile(<redacted>)"


class _ProfileRegistry:
    """현재 Registry를 읽고 Profile과 설정 범위만 Catalog에 노출한다."""

    def __init__(
        self, registry: Registry, servers: tuple[ServerSettings, ...], allowed: tuple[str, ...]
    ) -> None:
        self._registry = registry
        self._servers = {server.id: server for server in servers if server.id in allowed}

    def registration(self, server_id: str) -> RegisteredServer | None:
        settings = self._servers.get(server_id)
        if settings is None:
            return None
        record = self._registry.registration(server_id)
        if record is None:
            return None
        try:
            record = RegisteredServer.model_validate(record)
        except ValidationError:
            raise CatalogConfigError("Registry 등록 모델이 유효하지 않다") from None
        if (
            record.server_id != server_id
            or record.boundary.tier != settings.tier
            or record.boundary.max_label != settings.max_label
        ):
            raise CatalogConfigError("Registry와 서버 설정이 일치하지 않는다")
        configured = {tool.name: tool for tool in settings.tools}
        tools = tuple(tool for tool in record.tools if tool.tool_name in configured)
        for tool in tools:
            expected = configured[tool.tool_name]
            if (
                tool.kind != expected.kind
                or tool.resource_arg != expected.resource_arg
                or tool.destination_arg != expected.destination_arg
            ):
                raise CatalogConfigError("Registry와 도구 설정이 일치하지 않는다")
        return record.model_copy(update={"tools": tools})

    def status(self, server_id: str) -> RegStatus | None:
        return self._registry.status(server_id)

    def fingerprint(self, tool: ToolSpec) -> str | None:
        return self._registry.fingerprint(tool)


def load_profile_settings(
    document: Mapping[str, object],
    registry: Registry,
    *,
    resource_resolver: ResourceResolver | None = None,
) -> LoadedProfile:
    """이미 파싱한 JSON Mapping을 검증한다. 파일·URL·프로세스에 접근하지 않는다.

    입력은 profile(id/servers/destinations/sources), servers, destinations,
    policy(taint_mode/approval_ttl_seconds), access_rules(allow/deny)다.
    JSON 배열을 불변 tuple로 옮기며 생략된 기본값도 전체 설정 해시에 포함한다.
    접근 규칙은 normalize_access_rules로 정렬·중복 제거한다. 다른 배열의
    순서는 보존한다. 형식 결정 필요(C 경로 검증, A 시작 연결).
    """
    if not isinstance(document, Mapping):
        raise CatalogConfigError("Profile 설정은 JSON Mapping이어야 한다")
    try:
        snapshot = dict(document)
        jcs_canonicalize(snapshot)
        # JCS는 300.0을 300으로 쓰므로 검증 전 직렬화에는 원래 숫자 타입을 보존한다.
        config = _Configuration.model_validate_json(
            json.dumps(snapshot, ensure_ascii=False, allow_nan=False), strict=True
        )
        rules = AccessRules(allow=config.access_rules.allow, deny=config.access_rules.deny)
        if not rules.is_valid:
            raise CatalogConfigError("접근 규칙 설정이 유효하지 않다")
        _check_configuration(config)
        normalized = config.model_dump(mode="json")
        normalized["access_rules"] = normalize_access_rules(rules)
        version = digest(jcs_canonicalize(normalized))
        selected = config.profile
        profile = Profile(
            profile_id=selected.id,
            server_ids=selected.servers,
            destination_ids=selected.destinations,
            source_rules=tuple(source.as_rule() for source in selected.sources),
        )
        catalog = Catalog(
            _ProfileRegistry(registry, config.servers, profile.server_ids),
            profile_id=profile.profile_id,
            destinations=tuple(
                item.as_boundary()
                for item in config.destinations
                if item.id in profile.destination_ids
            ),
            source_rules=profile.source_rules,
            resource_resolver=resource_resolver,
        )
        guard_settings = GuardSettings(
            profile_id=profile.profile_id,
            policy_version=version,
            taint_mode=config.policy.taint_mode,
            approval_ttl_seconds=config.policy.approval_ttl_seconds,
        )
    except (ValidationError, JcsCanonicalizationError, SourceConfigError):
        raise CatalogConfigError("Profile 설정이 유효하지 않다") from None
    return LoadedProfile(profile, catalog, rules, guard_settings, config.servers)


def _check_configuration(config: _Configuration) -> None:
    servers = {server.id: server for server in config.servers}
    destinations = {destination.id for destination in config.destinations}
    selected = config.profile
    if (
        len(servers) != len(config.servers)
        or len(destinations) != len(config.destinations)
        or len(set(selected.servers)) != len(selected.servers)
        or len(set(selected.destinations)) != len(selected.destinations)
    ):
        raise CatalogConfigError("설정 ID는 중복될 수 없다")
    if (
        any(not valid_server_id(server) for server in selected.servers)
        or not set(selected.servers) <= servers.keys()
        or not set(selected.destinations) <= destinations
    ):
        raise CatalogConfigError("Profile 참조가 유효하지 않다")
    for source in selected.sources:
        server = servers.get(source.server)
        if (
            server is None
            or source.server not in selected.servers
            or not any(
                tool.name == source.tool and tool.kind is ToolKind.READ for tool in server.tools
            )
        ):
            raise CatalogConfigError("출처 규칙 참조가 유효하지 않다")
    for server_id, tool_name in (*config.access_rules.allow, *config.access_rules.deny):
        server = servers.get(server_id)
        if (
            not valid_server_id(server_id)
            or server is None
            or not any(tool.name == tool_name for tool in server.tools)
        ):
            raise CatalogConfigError("접근 규칙 참조가 유효하지 않다")
