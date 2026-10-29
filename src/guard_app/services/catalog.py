"""기술설계서 4장: 모델 제안을 신뢰 등록·Profile로 해석한다."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Self, cast

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

from guard_app.core.access_rules import AccessRules, normalize_access_rules
from guard_app.domain.contracts import RegisteredServer, ToolView
from guard_app.domain.enums import Label, RegStatus, ToolKind, TrustTier
from guard_app.domain.errors import ResolutionError
from guard_app.domain.json_types import FrozenJsonObject
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
from guard_app.security.endpoints import EndpointError, canonical_endpoint
from guard_app.services.catalog_schema import ArgumentSchema, CatalogConfigError
from guard_app.services.catalog_sources import (
    SourceClassifier,
    SourceConfigError,
    SourceRule,
    canonical_resource_parts,
    valid_server_id,
)

__all__ = [
    "SETTINGS_MAX_BYTES",
    "SETTINGS_MAX_DEPTH",
    "Catalog",
    "CatalogConfigError",
    "GuardSettings",
    "LoadedProfile",
    "Profile",
    "PreparedPolicyChange",
    "ResourceResolver",
    "ServerSettings",
    "SettingsFileError",
    "ToolSettings",
    "load_profile_settings",
    "parse_settings_bytes",
    "prepare_policy_change",
    "prepare_server_access_change",
    "serialize_settings_bytes",
]

type ResourceResolver = Callable[[ToolSpec, str], str]

SETTINGS_MAX_BYTES = 1_048_576
SETTINGS_MAX_DEPTH = 64


_SETTINGS_ERROR_CODES = frozenset(
    {
        "SETTINGS_LIMIT_INVALID",
        "SETTINGS_TOO_LARGE",
        "SETTINGS_ENCODING_INVALID",
        "SETTINGS_JSON_INVALID",
        "SETTINGS_DUPLICATE_KEY",
        "SETTINGS_NONFINITE_NUMBER",
        "SETTINGS_NUMBER_RANGE",
        "SETTINGS_TOO_DEEP",
        "SETTINGS_ENVELOPE_INVALID",
        "SETTINGS_VERSION_UNSUPPORTED",
    }
)


class SettingsFileError(ValueError):
    """CFG-01 실패의 고정 code만 담는다. 입력·경로·원래 예외는 인자로 받지 않는다."""

    def __init__(self, code: str) -> None:
        self.code = (
            code if type(code) is str and code in _SETTINGS_ERROR_CODES else "SETTINGS_JSON_INVALID"
        )
        super().__init__(self.code)


def parse_settings_bytes(
    data: bytes, *, max_bytes: int = SETTINGS_MAX_BYTES, max_depth: int = SETTINGS_MAX_DEPTH
) -> dict[str, object]:
    """CFG-01 v1 봉투를 벗긴 독립 JSON 사본. 파일·경로·Registry에 접근하지 않는다.

    바이트 상한에는 BOM·공백도 포함한다. 선두 UTF-8 BOM 한 개만 허용하고
    파싱 전에 문자열/escape를 구별해 깊이를 제한한다(봉투 1, settings 2).
    중복 키·비유한 수·surrogate·JCS 안전 범위 밖 정수는 거부한다.
    Profile 필드 검증과 policy_version 계산은 load_profile_settings가 맡는다.
    상한은 신뢰된 호출자만 정하며, 인터프리터의 재귀 한계도 넘을 수 없다.
    C의 크기 제한 읽기는 SETTINGS_MAX_BYTES + 1바이트까지만 읽어 넘긴다.
    기본 상한을 두 곳에 따로 적지 않고 공개 SETTINGS_MAX_* 상수를 공유한다.
    """
    _settings_limits(max_bytes, max_depth)
    if type(data) is not bytes:
        raise SettingsFileError("SETTINGS_ENCODING_INVALID")
    if len(data) > max_bytes:
        raise SettingsFileError("SETTINGS_TOO_LARGE")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SettingsFileError("SETTINGS_ENCODING_INVALID") from None
    _settings_text_depth(text, max_depth)
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_settings_pairs,
            parse_constant=_settings_constant,
            parse_int=_settings_integer,
        )
        envelope = _settings_json_copy(parsed, depth=0, max_depth=max_depth, active=set())
    except json.JSONDecodeError:
        raise SettingsFileError("SETTINGS_JSON_INVALID") from None
    except RecursionError:
        raise SettingsFileError("SETTINGS_TOO_DEEP") from None
    if (
        type(envelope) is not dict
        or set(envelope) != {"schema_version", "settings"}
        or type(envelope["schema_version"]) is not int
        or type(envelope["settings"]) is not dict
    ):
        raise SettingsFileError("SETTINGS_ENVELOPE_INVALID")
    if envelope["schema_version"] != 1:
        raise SettingsFileError("SETTINGS_VERSION_UNSUPPORTED")
    return cast(dict[str, object], envelope["settings"])


def serialize_settings_bytes(
    settings: Mapping[str, object],
    *,
    max_bytes: int = SETTINGS_MAX_BYTES,
    max_depth: int = SETTINGS_MAX_DEPTH,
) -> bytes:
    """독립 JSON 사본을 v1 봉투로 직렬화한다. 입력 보정·변경·파일 접근은 없다.

    저장 입력은 PreparedPolicyChange.document.to_dict()다. 해시용 정규화 사본
    _Configuration.model_dump(mode="json")은 등급을 숫자로 바꾸므로 쓰지 않는다.
    Profile 검증은 호출자가 먼저 수행한다. 문자열 키 정렬·들여쓰기 2칸·UTF-8
    (BOM 없음)·LF·끝 LF 한 개를 쓰고 배열 순서와 JSON 값 타입은 보존한다.
    내부 값은 정확한 dict/list/str/int/float/bool/None만 받는다. 깊이는 봉투를
    포함하고 읽기와 같은 숫자·문자 제한을 적용한다. pretty bytes는 JCS가 아니다.
    """
    _settings_limits(max_bytes, max_depth)
    if not isinstance(settings, Mapping):
        raise SettingsFileError("SETTINGS_ENVELOPE_INVALID")
    try:
        envelope = _settings_json_copy(
            {"schema_version": 1, "settings": dict(settings)},
            depth=0,
            max_depth=max_depth,
            active=set(),
        )
        data = (
            json.dumps(
                envelope,
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ": "),
            )
            + "\n"
        ).encode("utf-8")
    except RecursionError:
        raise SettingsFileError("SETTINGS_TOO_DEEP") from None
    if len(data) > max_bytes:
        raise SettingsFileError("SETTINGS_TOO_LARGE")
    return data


def _settings_limits(max_bytes: int, max_depth: int) -> None:
    if type(max_bytes) is not int or max_bytes < 1 or type(max_depth) is not int or max_depth < 1:
        raise SettingsFileError("SETTINGS_LIMIT_INVALID")


def _settings_text_depth(text: str, max_depth: int) -> None:
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise SettingsFileError("SETTINGS_TOO_DEEP")
        elif char in "]}":
            depth -= 1


def _settings_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SettingsFileError("SETTINGS_DUPLICATE_KEY")
        result[key] = value
    return result


def _settings_constant(value: str) -> object:
    raise SettingsFileError("SETTINGS_NONFINITE_NUMBER")


def _settings_integer(value: str) -> int:
    # JSON 정수 토큰은 leading zero가 없으므로 큰 int 생성 전에 거부할 수 있다.
    if len(value.removeprefix("-")) > 16:
        raise SettingsFileError("SETTINGS_NUMBER_RANGE")
    return int(value)


def _settings_json_copy(value: object, *, depth: int, max_depth: int, active: set[int]) -> object:
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise SettingsFileError("SETTINGS_ENCODING_INVALID")
        return value
    if type(value) is int:
        if not -(2**53 - 1) <= value <= 2**53 - 1:
            raise SettingsFileError("SETTINGS_NUMBER_RANGE")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise SettingsFileError("SETTINGS_NONFINITE_NUMBER")
        return value
    if type(value) not in (dict, list):
        raise SettingsFileError("SETTINGS_JSON_INVALID")
    depth += 1
    if depth > max_depth:
        raise SettingsFileError("SETTINGS_TOO_DEEP")
    identity = id(value)
    if identity in active:
        raise SettingsFileError("SETTINGS_JSON_INVALID")
    active.add(identity)
    try:
        if type(value) is list:
            return [
                _settings_json_copy(child, depth=depth, max_depth=max_depth, active=active)
                for child in value
            ]
        result: dict[str, object] = {}
        for key, child in cast(dict[object, object], value).items():
            if type(key) is not str:
                raise SettingsFileError("SETTINGS_JSON_INVALID")
            _settings_json_copy(key, depth=depth, max_depth=max_depth, active=active)
            result[key] = _settings_json_copy(
                child, depth=depth, max_depth=max_depth, active=active
            )
        return result
    finally:
        active.remove(identity)


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
        """ACTIVE·지원 종류·지문 일치 도구의 스키마와 검토된 설명만 노출한다.

        DESC-01 합의 전 제안: 원본 schema의 description 등으로 자동 대체하지 않는다.
        사용자 검토·저장은 A 입력/C Registry 책임이며 빈 값은 빈 설명 그대로다.
        """
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
                        description=tool.reviewed_description,
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
    """C의 경로·실행 파일 검증에 넘길 값. 로더는 실행하거나 접근하지 않는다.

    command_sha256·command_size는 등록 화면에서 C pin_executable이 계산하고
    사용자가 확인한 지문과 크기다. A 연결에서 두 값이 있으면
    ExecutablePin(command_size, command_sha256)으로 바꾸고 없으면 None을 넘긴다.
    """

    transport: Literal["stdio", "streamable_http"]
    command: NonEmptyString | None = None
    command_sha256: str | None = None
    command_size: int | None = None
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
        for name in ("command_sha256", "command_size"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError("실행 파일 고정값에 null을 쓸 수 없다")
        if self.command_sha256 is not None and (
            len(self.command_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.command_sha256)
        ):
            raise ValueError("실행 파일 지문 형식이 유효하지 않다")
        if self.command_size is not None and not 1 <= self.command_size <= 2**53 - 1:
            raise ValueError("실행 파일 크기가 유효하지 않다")
        if (self.command_sha256 is None) != (self.command_size is None):
            raise ValueError("실행 파일 지문과 크기는 함께 지정해야 한다")
        if self.transport == "stdio":
            if self.command is None or self.url is not None:
                raise ValueError("stdio 연결 설정이 유효하지 않다")
        elif (
            self.url is None
            or self.command is not None
            or self.args
            or self.env_allow
            or self.command_sha256 is not None
            or self.command_size is not None
        ):
            raise ValueError("HTTP 연결 설정이 유효하지 않다")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("도구 이름은 서버 안에서 중복될 수 없다")
        return self

    @model_serializer(mode="wrap")
    def serialize_server(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """미고정 필드만 생략해 기존 설정의 정책 해시를 유지한다."""
        values = cast(dict[str, object], handler(self))
        if self.command_sha256 is None:
            values.pop("command_sha256", None)
            values.pop("command_size", None)
        return values


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
    """검증된 독립 사본. servers는 해시에 포함된 전체 설정이며 선택 범위가 아니다.

    실행 시작(조립·목록 수집)에는 startable_servers를 사용하고 C 경로·실행 파일
    검증을 연결한다. selected_servers는 설정에서 고른 서버 그대로다.
    """

    profile: Profile
    catalog: Catalog
    access_rules: AccessRules
    guard_settings: GuardSettings
    servers: tuple[ServerSettings, ...]
    _profile_registry: _ProfileRegistry | None = None

    @property
    def policy_version(self) -> str:
        return self.guard_settings.policy_version

    @property
    def selected_servers(self) -> tuple[ServerSettings, ...]:
        """전체 설정 순서대로 Profile이 설정에서 선택한 서버를 그대로 반환한다."""
        return tuple(server for server in self.servers if server.id in self.profile.server_ids)

    @property
    def startable_servers(self) -> tuple[ServerSettings, ...]:
        """실행 시작(조립·목록 수집)에 쓸 선택 서버를 반환한다.

        selected_servers는 설정에서 고른 서버 그대로이며 실행 허가가 아니다.
        현재는 URL_* 이슈만 제외한다. A155 record_settings_change 순서 이관과
        C apply_settings 연결 후 REG_SETTINGS_MISMATCH도 제외할 계획이다.
        """
        return tuple(
            server
            for server in self.selected_servers
            if not (self.server_issue(server.id) or "").startswith("URL_")
        )

    def server_issue(self, server_id: str) -> str | None:
        """선택된 서버의 재확인 문제를 조회한다. 이 값은 실행 허가가 아니다.

        A가 저장된 값과 파일 값을 보여 준 뒤 record_settings_change, 설정 저장,
        정책 적용, 다시 활성화 순서로 처리한다.
        """
        if self._profile_registry is None:
            return None
        return self._profile_registry.server_issue(server_id)

    def __repr__(self) -> str:
        return "LoadedProfile(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPolicyChange:
    """부수 효과 없는 준비 결과. document.to_dict()로 저장할 독립 JSON 사본을 얻는다.

    준비 → 호출자(A·C)가 document 저장 → 저장 성공 후 Guard 적용 순서다.
    파일 형식·경로·저장 실패 처리는 호출자 책임이며 이 타입은 파일을 쓰지 않는다.
    """

    document: FrozenJsonObject
    loaded_profile: LoadedProfile
    previous_policy_version: str

    @property
    def policy_version(self) -> str:
        return self.loaded_profile.policy_version

    @property
    def changed(self) -> bool:
        return self.previous_policy_version != self.policy_version

    def __repr__(self) -> str:
        return "PreparedPolicyChange(<redacted>)"


def prepare_policy_change(
    current_document: Mapping[str, object],
    new_document: Mapping[str, object],
    registry: Registry,
    *,
    resource_resolver: ResourceResolver | None = None,
) -> PreparedPolicyChange:
    """전체 변경을 검증·준비한다. 두 문서·Registry·파일·Task를 수정하지 않는다.

    호출자가 반환 document를 저장한 뒤에만 Guard.apply_policy_change를 호출한다.
    저장 실패면 적용하지 않는다. 적용 뒤 새 Task는 새 Profile·버전으로 생성한다.
    """
    current = load_profile_settings(_settings_copy(current_document), registry)
    document = _settings_copy(new_document)
    loaded = load_profile_settings(document, registry, resource_resolver=resource_resolver)
    return PreparedPolicyChange(FrozenJsonObject(document), loaded, current.policy_version)


def prepare_server_access_change(
    current_document: Mapping[str, object],
    registry: Registry,
    *,
    server_id: str,
    tool_permissions: Mapping[str, bool],
    resource_resolver: ResourceResolver | None = None,
) -> PreparedPolicyChange:
    """활성화 확인의 전체 READ/SEND 체크 목록으로 해당 서버 규칙만 대체한다.

    지원 도구마다 정확한 bool 하나를 요구한다. 누락·OTHER·미설정 이름은 거부한다.
    준비 → 호출자 저장 → 저장 성공 후 적용 순서이며 기본 체크 표시는 A 책임이다.
    """
    document = _settings_copy(current_document)
    current = load_profile_settings(document, registry)
    if not valid_server_id(server_id) or not isinstance(tool_permissions, Mapping):
        raise CatalogConfigError("서버 활성화 접근 요청이 유효하지 않다")
    server = next((server for server in current.servers if server.id == server_id), None)
    if server is None:
        raise CatalogConfigError("서버 활성화 대상이 설정에 없다")
    supported = {tool.name for tool in server.tools if tool.kind in (ToolKind.READ, ToolKind.SEND)}
    choices = dict(tool_permissions)
    if (
        any(type(name) is not str for name in choices)
        or set(choices) != supported
        or any(type(allowed) is not bool for allowed in choices.values())
    ):
        raise CatalogConfigError("지원 도구의 전체 허용 체크 목록이 필요하다")
    rules = cast(dict[str, list[list[str]]], document.get("access_rules", {}))
    document["access_rules"] = {
        field: [pair for pair in rules.get(field, []) if pair[0] != server_id]
        + [[server_id, name] for name in sorted(choices) if choices[name] is allowed]
        for field, allowed in (("allow", True), ("deny", False))
    }
    return prepare_policy_change(
        current_document, document, registry, resource_resolver=resource_resolver
    )


def _settings_copy(document: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(document, Mapping):
        raise CatalogConfigError("정책 설정은 JSON Mapping이어야 한다")
    value = document.to_dict() if isinstance(document, FrozenJsonObject) else dict(document)
    # JSON 사본을 먼저 분리하고 상세 검증은 load_profile_settings에 맡긴다.
    try:
        return FrozenJsonObject(value).to_dict()
    except ValueError:
        raise CatalogConfigError("정책 설정이 유효하지 않다") from None


class _ProfileRegistry:
    """현재 Registry를 읽고 Profile과 설정 범위만 Catalog에 노출한다."""

    def __init__(
        self,
        registry: Registry,
        servers: tuple[ServerSettings, ...],
        allowed: tuple[str, ...],
        issues: Mapping[str, str] | None = None,
    ) -> None:
        self._registry = registry
        self._servers = {server.id: server for server in servers if server.id in allowed}
        self._issues = MappingProxyType(dict(issues or {}))

    def _matching_registration(self, server_id: str) -> tuple[RegisteredServer | None, bool]:
        settings = self._servers.get(server_id)
        if settings is None or server_id in self._issues:
            return None, False
        record = self._registry.registration(server_id)
        if record is None:
            return None, False
        try:
            record = RegisteredServer.model_validate(record)
        except ValidationError:
            raise CatalogConfigError("Registry 등록 모델이 유효하지 않다") from None
        if record.server_id != server_id:
            raise CatalogConfigError("Registry 조회 ID와 등록 서버 ID가 다르다")
        if record.boundary.tier != settings.tier or record.boundary.max_label != settings.max_label:
            return None, True
        configured = {tool.name: tool for tool in settings.tools}
        tools = tuple(tool for tool in record.tools if tool.tool_name in configured)
        for tool in tools:
            expected = configured[tool.tool_name]
            if (
                tool.kind != expected.kind
                or tool.resource_arg != expected.resource_arg
                or tool.destination_arg != expected.destination_arg
            ):
                return None, True
        return record.model_copy(update={"tools": tools}), False

    def registration(self, server_id: str) -> RegisteredServer | None:
        return self._matching_registration(server_id)[0]

    def server_issue(self, server_id: str) -> str | None:
        if server_id not in self._servers:
            return None
        if server_id in self._issues:
            return self._issues[server_id]
        _, mismatch = self._matching_registration(server_id)
        return "REG_SETTINGS_MISMATCH" if mismatch else None

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
    순서는 보존한다. CFG-01 파일 봉투는 parse_settings_bytes로 먼저 벗긴다.
    C 경로 검증·A 시작 연결은 별도다.
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
        issues: dict[str, str] = {}
        for server in config.servers:
            if server.transport == "streamable_http":
                try:
                    canonical_endpoint(server.url)
                except EndpointError as error:
                    issues[server.id] = cast(str, error.args[0])
        selected = config.profile
        profile = Profile(
            profile_id=selected.id,
            server_ids=selected.servers,
            destination_ids=selected.destinations,
            source_rules=tuple(source.as_rule() for source in selected.sources),
        )
        profile_registry = _ProfileRegistry(registry, config.servers, profile.server_ids, issues)
        catalog = Catalog(
            profile_registry,
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
    return LoadedProfile(profile, catalog, rules, guard_settings, config.servers, profile_registry)


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
