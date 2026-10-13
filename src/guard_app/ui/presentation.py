"""Fixed presentation vocabulary; no policy or state transitions live here."""

from __future__ import annotations

import unicodedata
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
    "AGT_DENY": "AGT 접근 거절",
    "AGT_ERROR": "AGT 결과 확인 실패",
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
    "RUN_CONTINUE_QUERY_REQUIRED": "호출 처리 완료 · '실행 / 다음 모델 단계'로 이어서 진행할 수 있어.",
    "RUN_COMPLETE_QUERY_REQUIRED": "모델 단계 완료 · 모델이 요청한 호출이 더 없어.",
    "RUN_PENDING_QUERY_REQUIRED": "승인이 필요한 호출이 있어 · '승인 사본 확인' 후 승인하거나 거절해.",
    "RUN_DENIED_QUERY_REQUIRED": (
        "호출이 차단됐어 · Guard 정책 또는 AGT가 허용하지 않아 실행하지 않았어. "
        "차단 사유 조회는 아직 이 화면에 연결되지 않았어."
    ),
    "RUN_HALTED_QUERY_REQUIRED": "Task가 정지됐어 · 이어서 실행할 수 없어.",
    "RUN_OUTCOME_UNKNOWN_QUERY_REQUIRED": (
        "전송 결과를 확인할 수 없어 · 수신측에서 실제 반영 여부를 확인해. 자동 재시도는 하지 않아."
    ),
    "RUN_MODEL_ERROR_QUERY_REQUIRED": "모델 응답 형식 오류로 실행을 멈췄어.",
    "RUN_CLOSED_QUERY_REQUIRED": "실행기가 닫혀 이 Task는 더 진행할 수 없어.",
    # The runner reports "stopped" for cancel, epoch replacement, Store
    # failure, and tool configuration errors alike; do not name one cause.
    "RUN_STOPPED_QUERY_REQUIRED": (
        "실행이 정지됐어 · 이 Task는 더 진행할 수 없어. 계속하려면 새 Task가 필요할 수 있어."
    ),
    "RUN_GUARD_ERROR_QUERY_REQUIRED": (
        "Guard 내부 오류로 이 Task 실행을 멈췄어 · 이어서 실행할 수 없어. "
        "앱을 다시 시작하고 관리자에게 확인해."
    ),
    "RUN_WITHHELD_QUERY_REQUIRED": (
        "도구 결과를 공개하지 않았어 · 모델에는 결과 대신 비공개 안내만 전달했어. "
        "Task는 계속 실행 중이라 새 사용자 지시로 이어갈 수 있어."
    ),
    "RUNNER_UNAVAILABLE": "이 실행에서 다룰 수 없는 작업이야 · 실행·제어하지 않았어.",
    "RUNNER_UNCONFIRMED": "실행 결과를 확인하지 못했어 · 위 Task 상태를 확인해.",
}
APPROVAL_RESULTS = {
    "CONTROL_RELEASED_QUERY_REQUIRED": "승인한 호출을 실행했어.",
    "CONTROL_PENDING_QUERY_REQUIRED": "승인 처리 후 다음 호출에 다시 승인이 필요해.",
    "CONTROL_DENIED_QUERY_REQUIRED": "호출은 실행되지 않았어 · 거절, 만료 또는 상태 변경으로 처리가 끝났어.",
    "CONTROL_WITHHELD_QUERY_REQUIRED": "호출은 실행됐지만 결과를 분류할 수 없어 공개하지 않았어.",
    "CONTROL_OUTCOME_UNKNOWN_QUERY_REQUIRED": (
        "전송 결과를 확인할 수 없어 · 수신측에서 실제 반영 여부를 확인해."
    ),
    "CONTROL_GUARD_ERROR_QUERY_REQUIRED": (
        "제어 처리 중 Guard 내부 오류가 발생했어 · 이 Task 실행은 이어갈 수 없어. "
        "Task 상태는 위에 다시 조회된 표시를 기준으로 확인해."
    ),
    "REVIEW_READY": "승인 사본을 불러왔어 · 아래 전체 인자를 확인한 뒤 승인해.",
    "REVIEW_UNAVAILABLE": "승인 사본을 불러올 수 없어 · 이미 처리됐거나 만료됐을 수 있어.",
}
# Bridge lifecycle codes -> current backend state for the operator. The code
# itself stays visible only as secondary text.
LIFECYCLE_MESSAGES = {
    "PROCESS_RESTART_REQUIRED": "정리 확인 실패 · 프로그램을 다시 시작해야 해.",
    "BACKEND_ALREADY_ACTIVE": "이전 백엔드가 아직 종료되지 않았어 · 프로그램을 다시 시작해야 해.",
    "STARTING": "백엔드 시작 중 · 준비되면 실행할 수 있어.",
    "READY": "백엔드 준비 완료.",
    "CLOSING": "백엔드 종료 중 · 새 요청은 받지 않아.",
    "CLOSED": "백엔드 종료됨 · 더 실행할 수 없어.",
    "CLEANUP_UNCONFIRMED": "백엔드 종료됨 · 정리 완료는 확인하지 못했어.",
    "START_THREAD_FAILED": "백엔드를 시작하지 못했어 · 앱을 다시 시작해야 해.",
    "STARTUP_FAILED": "백엔드를 시작하지 못했어 · 앱을 다시 시작해야 해.",
    "INITIAL_SNAPSHOT_FAILED": (
        "백엔드를 시작하지 못했어 · Task 상태를 불러오지 못해 앱을 다시 시작해야 해."
    ),
    "STARTUP_CLEANUP_UNCONFIRMED": (
        "백엔드를 시작하지 못했고 정리 완료도 확인하지 못했어 · 앱을 다시 시작해."
    ),
    "BACKEND_FAILURE": "백엔드 오류로 멈췄어 · 더 실행할 수 없어. 위 Task 상태를 확인해.",
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
    "server_add": "MCP 추가 초안",
    "policy": "정책 · 경계",
    "audit": "감사 기록",
    "runs": "에이전트 실행",
    "settings": "설정",
}


def fixed(mapping: dict[str, str], value: str | None, *, default: str = "미확인") -> str:
    """Avoid echoing an unknown backend status or reason as arbitrary text."""

    return mapping.get(value, default) if value is not None else MISSING


def format_kst(value: datetime | None) -> str:
    """Show an explicit full date; naive/invalid timestamps have no assumed zone."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    return value.astimezone(_KST).strftime("%Y년 %m월 %d일 %H:%M:%S KST")


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
