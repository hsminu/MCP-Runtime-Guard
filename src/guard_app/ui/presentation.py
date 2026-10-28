"""Fixed presentation vocabulary; no policy or state transitions live here."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

# Current operational records use KST (UTC+09:00). This avoids a runtime tzdata
# dependency on Windows; historical Korea daylight-saving dates need a separate
# Asia/Seoul database decision before they can be displayed as local time.
_KST = timezone(timedelta(hours=9), name="KST")
MISSING = "미제공"

LABELS = {
    "PUBLIC": ("공개", "PUBLIC", "public"),
    "INTERNAL": ("내부", "INTERNAL", "internal"),
    "CONFIDENTIAL": ("기밀", "CONFIDENTIAL", "confidential"),
}
TIERS = {
    "internal": "내부 · internal",
    "approved_external": "승인된 외부 · approved_external",
    "general_external": "일반 외부 · general_external",
}
REGISTRY_STATUSES = {
    "active": "ACTIVE · 등록 활성",
    "pending": "PENDING · 등록 검토 중",
    "blocked": "BLOCKED · 등록 차단",
}
# Lists and summaries show Korean words only; the bilingual maps above stay for
# details, logs and exports, and the code goes in a tooltip (fixed_code).
# Display names from config/codex/model_catalog.json; unknown ids show as is.
MODEL_NAMES = {"gpt-6.1-sol": "GPT-6.1-Sol"}
EFFORT_NAMES = {
    "low": "낮음",
    "medium": "보통",
    "high": "높음",
    "xhigh": "매우 높음",
    "max": "최대",
}

TIER_NAMES = {
    "internal": "내부",
    "approved_external": "승인된 외부",
    "general_external": "일반 외부",
}
REGISTRY_NAMES = {
    "active": "등록 활성",
    "pending": "등록 검토 중",
    "blocked": "등록 차단",
}
SESSION_STATUSES = {
    "connected": "연결됨",
    "disconnected": "연결 안 됨",
    "connecting": "연결 중",
    "error": "연결 오류",
}
TRANSPORTS = {
    "stdio": "stdio",
    "streamable_http": "Streamable HTTP",
}
ATTENTION_KINDS = {
    "approval": "승인 요청 검토",
    "blocked": "차단 사유 확인",
    "outcome_unknown": "수신 결과 확인",
    "fingerprint_changed": "도구 정의 변경 확인",
    "registration": "서버 등록 확인",
}
REASONS = {
    "INTERNAL_TO_EXTERNAL": "내부 자료의 외부 전송에 승인 필요",
    "CONFIDENTIAL_TO_EXTERNAL": "기밀 자료의 외부 전송 차단",
    "LABEL_EXCEEDS_BOUNDARY": "서버 또는 목적지의 최대 수신 등급 초과",
    "UNKNOWN_DESTINATION": "목적지 경계 미확인",
    "UNKNOWN_SERVER": "서버 등록 미확인",
    "TOOL_UNSUPPORTED": "등록되지 않은 도구",
    "INVALID_ARGUMENTS": "도구 인자 형식 오류",
    "REG_NOT_ACTIVE": "서버 등록 상태가 ACTIVE가 아님",
    "TOOL_FINGERPRINT_CHANGED": "도구 정의 지문 변경",
    "TASK_UNCLASSIFIED": "자료 등급을 분류할 수 없음",
    "TASK_NOT_RUNNING": "Task 실행 상태가 아님",
    "POLICY_CHANGED": "정책 변경으로 재평가 필요",
    "STATE_MISSING": "신뢰 상태를 확인할 수 없음",
    "ACCESS_DENY": "접근 규칙 거절",
    "ACCESS_ERROR": "접근 규칙 확인 실패",
    "INTERNAL_ERROR": "내부 처리 오류",
    "UNSUPPORTED_CONTENT": "지원하지 않는 응답 형식",
    "STATE_COMMIT_FAILED": "보안 상태 저장 실패",
    "RESULT_UNCLASSIFIED": "응답 자료를 분류할 수 없음",
    "POST_DISPATCH_ERROR": "전송 후 결과 확인 실패",
    "APPROVAL_CONTEXT_LOST": "승인 요청 맥락 손실",
    "APPROVAL_STALE": "승인 요청 상태 변경",
    "APPROVAL_RACE": "승인 요청 중복 처리",
}
DECISIONS = {"ALLOW": "허용", "ASK": "승인 필요", "DENY": "차단"}
# Backend command codes -> what happened and what the operator can do next.
# The code itself stays visible only as secondary text.
RUN_RESULTS = {
    "RUN_CONTINUE_QUERY_REQUIRED": "호출 처리 완료 · '실행 / 다음 모델 "
    "단계'로 이어서 진행할 수 있음.",
    "RUN_COMPLETE_QUERY_REQUIRED": "모델 단계 완료 · 모델이 요청한 호출이 더 없음.",
    "RUN_PENDING_QUERY_REQUIRED": "승인이 필요한 호출이 있음 · '승인 사본 "
    "확인' 후 승인하거나 거절하세요.",
    "RUN_DENIED_QUERY_REQUIRED": (
        "호출이 차단됨 · Guard 정책 또는 접근 규칙이 허용하지 않아 실행하지 않음. "
        "차단 사유 조회는 아직 이 화면에 연결되지 않음."
    ),
    "RUN_HALTED_QUERY_REQUIRED": "Task가 정지됨 · 이어서 실행할 수 없음.",
    "RUN_OUTCOME_UNKNOWN_QUERY_REQUIRED": (
        "전송 결과를 확인할 수 없음 · 수신측에서 실제 반영 여부를 확인하세요. "
        "자동 재시도는 하지 않음."
    ),
    "RUN_MODEL_ERROR_QUERY_REQUIRED": "모델 응답 형식 오류로 실행을 멈춤.",
    "RUN_CLOSED_QUERY_REQUIRED": "실행기가 닫혀 이 Task는 더 진행할 수 없음.",
    # The runner reports "stopped" for cancel, epoch replacement, Store
    # failure, and tool configuration errors alike; do not name one cause.
    "RUN_STOPPED_QUERY_REQUIRED": (
        "실행이 정지됨 · 이 Task는 더 진행할 수 없음. 계속하려면 새 Task가 필요할 수 있음."
    ),
    "RUN_GUARD_ERROR_QUERY_REQUIRED": (
        "Guard 내부 오류로 이 Task 실행을 멈춤 · 이어서 실행할 수 없음. "
        "Task 상태를 확인하고 새 작업을 시작하세요. 문제가 반복되면 앱을 다시 시작하세요."
    ),
    "RUN_WITHHELD_QUERY_REQUIRED": (
        "도구 결과를 공개하지 않음 · 모델에는 결과 대신 비공개 안내만 전달함. "
        "Task 상태와 이어서 실행할 수 있는지는 다시 조회된 표시를 확인하세요."
    ),
    "RUNNER_UNAVAILABLE": "이 실행에서 다룰 수 없는 작업임 · 실행·제어하지 않음.",
    "RUNNER_UNCONFIRMED": "실행 결과를 확인하지 못함 · 위 Task 상태를 확인하세요.",
}
APPROVAL_RESULTS = {
    "CONTROL_RELEASED_QUERY_REQUIRED": "승인한 호출을 실행함.",
    "CONTROL_PENDING_QUERY_REQUIRED": "승인 처리 후 다음 호출에 다시 승인이 필요함.",
    "CONTROL_DENIED_QUERY_REQUIRED": "호출은 실행되지 않음 · 거절, "
    "만료 또는 상태 변경으로 처리가 끝남.",
    "CONTROL_WITHHELD_QUERY_REQUIRED": (
        "호출은 실행됐지만 결과를 공개하지 않음 · "
        "공개 조건을 확인하지 못한 결과는 보여주지 않음. "
        "Task 상태는 위에 다시 조회된 표시를 확인하세요."
    ),
    "CONTROL_OUTCOME_UNKNOWN_QUERY_REQUIRED": (
        "전송 결과를 확인할 수 없음 · 수신측에서 실제 반영 여부를 확인하세요."
    ),
    "CONTROL_GUARD_ERROR_QUERY_REQUIRED": (
        "제어 처리 중 Guard 내부 오류가 발생함 · 이 Task 실행은 이어갈 수 없음. "
        "Task 상태는 위에 다시 조회된 표시를 기준으로 확인하세요."
    ),
    "REVIEW_READY": "승인 사본을 불러옴 · 아래 전체 인자를 확인한 뒤 승인하세요.",
    "REVIEW_UNAVAILABLE": "승인 사본을 불러올 수 없음 · 이미 처리됐거나 만료됐을 수 있음.",
}
# Bridge lifecycle codes -> current backend state for the operator. The code
# itself stays visible only as secondary text.
LIFECYCLE_MESSAGES = {
    "PROCESS_RESTART_REQUIRED": "정리 확인 실패 · 프로그램을 다시 시작하세요.",
    "BACKEND_ALREADY_ACTIVE": "이전 백엔드가 아직 종료되지 않음 · 프로그램을 다시 시작하세요.",
    "STARTING": "백엔드 시작 중 · 준비되면 실행할 수 있음.",
    "READY": "백엔드 준비 완료.",
    "CLOSING": "백엔드 종료 중 · 새 요청은 받지 않음.",
    "CLOSED": "백엔드 종료됨 · 더 실행할 수 없음.",
    "CLEANUP_UNCONFIRMED": "백엔드 종료됨 · 정리 완료는 확인하지 못함.",
    "START_THREAD_FAILED": "백엔드를 시작하지 못함 · 앱을 다시 시작하세요.",
    "STARTUP_FAILED": "백엔드를 시작하지 못함 · 앱을 다시 시작하세요.",
    "INITIAL_SNAPSHOT_FAILED": (
        "백엔드를 시작하지 못함 · Task 상태를 불러오지 못해 앱을 다시 시작하세요."
    ),
    "STARTUP_CLEANUP_UNCONFIRMED": (
        "백엔드를 시작하지 못했고 정리 완료도 확인하지 못함 · 앱을 다시 시작하세요."
    ),
    "BACKEND_FAILURE": "백엔드 오류로 멈춤 · 더 실행할 수 없음. 위 Task 상태를 확인하세요.",
}
REQUEST_STATUSES = {
    "DECIDED": "판정 완료",
    "WAITING_APPROVAL": "승인 대기",
    "DISPATCHING": "전송 중",
    "SUCCEEDED": "실행 완료",
    "WITHHELD": "결과 미공개",
    "FAILED": "실행 실패",
    "UNKNOWN_OUTCOME": "결과 불명 · 수신측 확인 필요",
    "DENIED": "차단",
}
PAGES = {
    "overview": "현황",
    "logs": "호출 로그",
    "approvals": "승인 대기",
    "servers": "MCP 서버",
    "server_add": "MCP 서버 추가",
    "policy": "정책 · 경계",
    "audit": "감사 기록",
    "runs": "대화",
    "results": "결과 · 실행 활동",
    "settings": "설정",
}

# Fixed capability failures are shared by draft creation and existing Task views.
# Backend exception text, filesystem paths and unknown codes are never echoed.
_CODEX_FAILURE_DETAILS = {
    "CODEX_SESSION_UNAVAILABLE": "Codex 연결이 준비되지 않음.",
    "CODEX_SESSION_INVALID": "Codex 연결을 확인하지 못함.",
    "CODEX_L2_UNAVAILABLE": "Codex 시작 검사가 준비되지 않음.",
    "CODEX_L2_FAILED": "Codex 시작 검사를 통과하지 못함.",
    "CODEX_CONFIG_UNAVAILABLE": "Codex 설정 검증이 준비되지 않음.",
    "CODEX_CONFIG_MISMATCH": "Codex 설정이 시작 검사에서 확인한 설정과 다름.",
    "CODEX_PREPARATION_FAILED": "Codex 세션 준비를 완료하지 못함.",
    "CODEX_CLEANUP_UNCONFIRMED": "Codex 종료·임시 자료 정리 완료를 확인하지 못함.",
    "CODEX_SIGNING_OUT": "Codex 로그아웃이 진행 중임.",
    "GUARD_PROFILE_UNAVAILABLE": "Guard 정책(Profile) 설정이 아직 연결되지 않음.",
    "POLICY_APPLY_FAILED": "저장한 정책을 적용하지 못해 Guard를 정지함.",
    "POLICY_SAVE_UNKNOWN": "정책 저장 결과를 확인하지 못해 Guard를 정지함.",
    "SETTINGS_FILE_MISSING": "설정 파일이 없어 Guard 정책(Profile)이 없음.",
    "SETTINGS_PROFILE_INVALID": "설정 파일의 Guard 정책(Profile) 내용이 올바르지 않음.",
    "REGISTRY_UNAVAILABLE": "서버 등록 기록을 불러오지 못함.",
}
# What the start made of the CFG-01 settings file (settings screen). The file
# location is fixed (C2.13), never a path the backend reported.
SETTINGS_FILE_LOCATION = r"%LOCALAPPDATA%\MCPRuntimeGuard\config\settings.json"
SETTINGS_FILE_PENDING = "설정 파일을 아직 확인하지 않음."
SETTINGS_FILE_NOT_READ = "이 실행은 설정 파일을 읽지 않음(시연)."
SETTINGS_FILE_STATUS = {
    "SETTINGS_LOADED": "설정 파일을 읽고 Guard 정책(Profile)을 확인함.",
    "SETTINGS_FILE_MISSING": (
        "설정 파일이 없음(첫 실행). Guard 정책이 없어 새 세션을 시작할 수 없음. "
        "앱을 닫은 상태에서 설정 파일을 만들고 다시 시작하세요."
    ),
    "SETTINGS_PROFILE_INVALID": (
        "설정 파일의 Guard 정책(Profile) 내용이 올바르지 않아 새 세션을 시작할 수 없음. "
        "앱을 닫은 상태에서 설정 파일을 고치세요."
    ),
    "REGISTRY_UNAVAILABLE": (
        "서버 등록 기록을 불러오지 못해 설정 파일을 확인하지 못함. 앱을 다시 시작하세요."
    ),
}
_CODEX_MISSING_COMPONENTS = frozenset(
    {
        "CODEX_SESSION_UNAVAILABLE",
        "CODEX_L2_UNAVAILABLE",
        "CODEX_CONFIG_UNAVAILABLE",
        "GUARD_PROFILE_UNAVAILABLE",
    }
)
# Bridge refusals before any backend work: nothing was run for these commands.
COMMAND_REFUSALS = {
    "NOT_READY": "요청을 접수하지 않음 · 백엔드가 아직 준비되지 않음. 준비된 뒤 다시 보내세요.",
    "CLOSING": "요청을 접수하지 않음 · 앱이 종료 중임.",
    "INGRESS_FULL": "요청을 접수하지 않음 · 처리 대기 요청이 가득 참. 잠시 뒤 다시 보내세요.",
    "QUEUE_FULL": "요청을 접수하지 않음 · 처리 대기 요청이 가득 참. 잠시 뒤 다시 보내세요.",
    "COMMAND_ID_CONFLICT": "요청을 접수하지 않음 · 같은 요청이 이미 처리 중임.",
}


# A session stopped while running (A88). Each text is complete on its own.
_CODEX_STOP_NOTICES = {
    "CODEX_L3_VIOLATION": (
        "Codex가 허용되지 않은 기능이나 승인을 요청해 이 Task를 정지함. "
        "새 세션에서 다시 시작하세요."
    ),
    "CODEX_USAGE_LIMIT": (
        "Codex 사용량 한도에 도달해 이 Task를 정지함. 한도가 풀린 뒤 새 세션을 시작하세요."
    ),
    "CODEX_TOOL_STOPPED": (
        "도구 결과를 공개할 수 없거나 전송 결과를 확인할 수 없어 이 Task를 정지함. "
        "결과 · 실행 활동에서 상태를 확인하세요."
    ),
    "CODEX_WATCHDOG_TIMEOUT": (
        "도구 호출이 제한 시간 안에 끝나지 않아 이 Task를 정지함. "
        "전송이었다면 수신측에서 반영 여부를 확인하세요."
    ),
    "CODEX_TOOL_CALL_INVALID": "Codex가 형식이 맞지 않는 도구 호출을 보내 이 Task를 정지함.",
    "CODEX_TURN_FAILED": "Codex 응답이 실패해 이 Task를 정지함. 새 세션에서 다시 시작하세요.",
    "CODEX_TURN_INTERRUPTED": "Codex 응답이 중단돼 이 Task를 정지함. 새 세션에서 다시 시작하세요.",
    "CODEX_START_FAILED": "Codex 세션을 시작하지 못함. 새 세션에서 다시 시작하세요.",
    "CODEX_ANSWER_TOO_LARGE": "Codex 답변이 표시 상한을 넘어 이 Task를 정지함.",
    "CODEX_INPUT_INVALID": "보낼 수 없는 메시지라 이 Task를 정지함. 새 세션에서 다시 시작하세요.",
    "CODEX_INTERRUPT_UNCONFIRMED": (
        "Codex 중단을 확인하지 못함. 앱을 종료하고 정리 상태를 확인하세요."
    ),
    "CODEX_SESSION_FAILED": "Codex 세션이 실패해 이 Task를 정지함. 새 세션에서 다시 시작하세요.",
}


def codex_failure_notice(code: str, *, creating: bool = False) -> str:
    """Describe only known failures; cleanup uncertainty never implies no launch."""

    stopped = _CODEX_STOP_NOTICES.get(code)
    if stopped is not None and not creating:
        return stopped
    detail = _CODEX_FAILURE_DETAILS.get(code)
    if detail is None:
        return ""
    if code == "CODEX_CLEANUP_UNCONFIRMED":
        return detail + " 자동으로 다시 실행하지 않음. 앱을 종료하고 정리 상태를 확인하세요."
    if code == "CODEX_SIGNING_OUT":
        return detail + " 세션을 시작하지 않았음. 로그아웃이 끝난 뒤 다시 시작하세요."
    context = " 세션을 시작하지 않았음." if creating else " 이 Task를 실행할 수 없음."
    if code in _CODEX_MISSING_COMPONENTS:
        # The build lacks the component; restarting or editing settings cannot help.
        return detail + context + " 이 앱 구성에서는 아직 이 기능을 쓸 수 없음."
    return detail + context + " 앱 설정을 확인하고 앱을 다시 시작하세요."


def task_label_text(label: object, unclassified: bool) -> str:
    """Store-confirmed Task watermark; never inferred from model or tool text.

    Korean words only; the enum name belongs in details and logs."""

    name = getattr(label, "name", None)
    text = LABELS.get(name) if type(name) is str else None
    shown = "미제공" if text is None else text[0]
    return f"Task 등급: {shown}" + (" · 미분류 자료 표시됨" if unclassified is True else "")


def session_blocker_notice(code: str) -> str:
    """Before any click: why new sessions are already known to be refused."""

    if not code:
        return ""
    if code == "SESSION_CREATION_UNAVAILABLE":
        return "이 실행 구성에는 새 Task 생성 기능이 연결되지 않음."
    detail = _CODEX_FAILURE_DETAILS.get(code)
    if detail is None:
        return "지금은 새 세션을 시작할 수 없음."
    if code == "CODEX_CLEANUP_UNCONFIRMED":
        return detail + " 새 세션을 시작하지 않음. 앱을 종료하고 정리 상태를 확인하세요."
    if code == "CODEX_SIGNING_OUT":
        return detail + " 로그아웃이 끝난 뒤 새 세션을 시작하세요."
    if code in _CODEX_MISSING_COMPONENTS:
        return detail + " 이 앱 구성에서는 아직 새 세션을 시작할 수 없음."
    return detail + " 지금은 새 세션을 시작할 수 없음. 앱 설정을 확인하고 앱을 다시 시작하세요."


def fixed(mapping: dict[str, str], value: str | None, *, default: str = "미확인") -> str:
    """Avoid echoing an unknown backend status or reason as arbitrary text."""

    return mapping.get(value, default) if value is not None else MISSING


def fixed_code(mapping: Mapping[str, object], value: str | None) -> str:
    """The enum code of a known vocabulary value for a tooltip; never echo others."""

    return value if value is not None and value in mapping else ""


def format_kst(value: datetime | None) -> str:
    """Show an explicit full date; naive/invalid timestamps have no assumed zone."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    return value.astimezone(_KST).strftime("%Y년 %m월 %d일 %H:%M:%S KST")


def format_kst_short(value: datetime | None) -> str:
    """Month, day and minute for list rows; format_kst keeps the full stamp."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    local = value.astimezone(_KST)
    return f"{local.month}월 {local.day}일 {local:%H:%M}"


# Letters and symbols that render as blank space although Unicode does not
# classify them as controls: Hangul fillers and the braille blank pattern.
INVISIBLE_LETTERS = frozenset({0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800})


def is_hidden_char(char: str) -> bool:
    """Controls, format (bidi, zero-width), line separators, blank lookalikes."""

    return (
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
        or ord(char) in INVISIBLE_LETTERS
    )


def public_display_text(value: str | None) -> str:
    """Render a public identifier or name on one line, exposing hidden chars.

    A newline, bidi override, or zero-width character must not look like a
    separate row, reordered text, or a different ID in a cell or card. This is
    display-only: the original value and navigation/selection IDs are unchanged.
    """

    if not value:
        return MISSING
    return "".join(f"[U+{ord(char):04X}]" if is_hidden_char(char) else char for char in value)
