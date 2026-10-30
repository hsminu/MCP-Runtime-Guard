"""Scanner 발견 코드를 사용자가 읽을 분류로 바꾼다(C4.22, C 소유).

`ScanReport`의 `Finding.code`는 Cisco Scanner가 붙인 위협 이름이다(C4.9 번역: SDK `threat_type`,
없으면 `threat_category`, 형식 밖이면 `SCAN_CODE_UNRECOGNIZED`). 설치본 4.8.6에서 LLM·YARA
분석기가 내는 이름은 `PROMPT INJECTION`·`TOOL POISONING` 등 열 개 남짓이고, 표기가 띄어쓰기와
밑줄로 섞여 있다. 화면(A)은 이 함수로 분류 이름·뜻·권장 조치 한 줄씩을 보이고, 원래 코드는 자세히
영역에만 둔다.

- 대소문자와 띄어쓰기·밑줄 차이는 같은 코드로 본다.
- 표에 없는 코드는 모두 "기타 위험 신호"다. 모르는 코드를 낮은 위험으로 바꾸지 않는다.
- 분류는 표시용이다. 위험도(`severity`)·활성화·승인·Guard 판정을 바꾸지 않는다(2026-10-15 결정).
- 문구에 "안전"이라고 쓰지 않는다. 발견이 없거나 검사가 실패해도 안전하다는 뜻이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScanCategory:
    """화면에 보일 분류 이름, 뜻 한 줄, 권장 조치 한 줄."""

    name: str
    meaning: str
    action: str


MODEL_STEERING = ScanCategory(
    "모델 조종",
    "도구 설명에 모델에게 내리는 지시문이 들어 있음",
    "설명을 직접 읽어 보고, 지시문이 있으면 이 도구를 끄세요",
)
TOOL_DISGUISE = ScanCategory(
    "도구 설명 오염·위장",
    "설명에 숨은 지시가 있거나 다른 도구인 척함",
    "이름·설명이 실제 기능과 맞는지 확인하고, 모르겠으면 끄세요",
)
DATA_LEAK = ScanCategory(
    "정보 유출",
    "자료를 바깥으로 보내는 기능이나 표현이 있음",
    "꼭 필요하지 않으면 끄고, 켠다면 보내는 곳 등록을 확인하세요",
)
CREDENTIALS = ScanCategory(
    "인증정보 요구",
    "키·비밀번호·토큰을 요구하거나 빼내려는 표현이 있음",
    "이 도구에 인증정보를 넣지 말고, 필요 없으면 끄세요",
)
EXECUTION = ScanCategory(
    "명령·코드 실행",
    "명령·코드를 실행하거나 파일·설정을 바꾸게 하는 표현이 있음",
    "서버 출처를 확인하기 전에는 활성화하지 마세요",
)
OTHER = ScanCategory(
    "기타 위험 신호",
    "분류표에 없는 위험 신호",
    "자세히에서 원래 코드를 확인하세요",
)
SCAN_CATEGORIES = (MODEL_STEERING, TOOL_DISGUISE, DATA_LEAK, CREDENTIALS, EXECUTION, OTHER)

# 설치본 4.8.6의 LLM·YARA 위협 이름(`ThreatMapping.LLM_THREATS`·`YARA_THREATS`)과
# `threat_type`이 없을 때 쓰이는 분류(`scanner_category`). 시험이 설치본과 대조한다.
_BY_CODE = {
    "PROMPT INJECTION": MODEL_STEERING,
    "TOOL POISONING": TOOL_DISGUISE,
    "TOOL SHADOWING": TOOL_DISGUISE,
    "DATA EXFILTRATION": DATA_LEAK,
    "CREDENTIAL HARVESTING": CREDENTIALS,
    "CODE EXECUTION": EXECUTION,
    "SUSPICIOUS CODE EXECUTION": EXECUTION,
    "INJECTION ATTACK": EXECUTION,
    "SYSTEM MANIPULATION": EXECUTION,
}


def scan_category(code: str) -> ScanCategory:
    """발견 코드의 분류. 표에 없거나 문자열이 아니면 `OTHER`다."""
    if type(code) is not str:
        return OTHER
    return _BY_CODE.get(" ".join(code.replace("_", " ").upper().split()), OTHER)
