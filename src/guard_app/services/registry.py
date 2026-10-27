"""서버 등록 Registry(C, 개발문서 10.1·2.1). 이번 단계(C4.12)는 도구 지문 계산이다.

지문은 FP-01(2026-10-18·10-26 결정)대로 A transport가 SDK로 다시 만든 정의 dict
(`ToolListing.tools`, `model_dump(mode="json", by_alias=True, exclude_unset=True)`)에 B의
`tool_fingerprint`(RFC 8785 JCS, SHA-256)를 적용한 값이다. 등록(C4.10)과 연결 직후 대조(A
`live_fingerprints`)가 이 함수 하나를 쓰면, 한쪽만 계산이 바뀌어 정상 서버가 모두 PENDING이
되는 일이 없다.

목록이 미완료이거나, 도구 이름이 없거나 겹치거나, JCS 정규화가 실패하면 지문을 하나도 만들지
않는다(fail-closed). 정의는 신뢰하지 않는 서버 데이터라 오류에 서버 텍스트를 싣지 않는다.

transport 패키지는 `services.product_assembly`만 가져온다(I1, A 구조 테스트). 그래서 목록은
`ToolListing`을 가져오지 않고 같은 모양(`ToolListingView`)으로 받는다.
"""

from __future__ import annotations

from typing import Protocol

from guard_app.domain.json_types import FrozenJsonObject
from guard_app.security.digest import JcsCanonicalizationError, tool_fingerprint


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
