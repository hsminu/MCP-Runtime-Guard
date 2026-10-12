"""Fixed presentation vocabulary; no policy or state transitions live here."""

from __future__ import annotations

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


def fixed(
    mapping: dict[str, str], value: str | None, *, default: str = "미확인"
) -> str:
    """Avoid echoing an unknown backend status or reason as arbitrary text."""

    return mapping.get(value, default) if value is not None else MISSING


def format_kst(value: datetime | None) -> str:
    """Show an explicit full date; naive/invalid timestamps have no assumed zone."""

    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return MISSING
    return value.astimezone(_KST).strftime("%Y년 %m월 %d일 %H:%M:%S KST")
