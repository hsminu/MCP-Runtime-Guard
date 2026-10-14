"""승인 사건의 UTC 기록 순서 보정. 실제 UTC/monotonic 만료 판정은 별도로 한다."""

from datetime import datetime, timedelta

from guard_app.domain.contracts import ApprovalRecord


def approval_event_time(record: ApprovalRecord, now_utc: datetime) -> datetime:
    """시계 역행으로 기록이 생성/앞선 결정보다 빨라지지 않도록 한다.

    이 값으로 expires_at/deadline을 연장하거나 만료된 승인에 권한을 부여하지 않는다.
    승인 서비스는 실제 시계로 만료를 먼저 검사하고 Store는 fresh UTC를 다시 검사한다.
    """
    if (
        type(now_utc) is not datetime
        or now_utc.tzinfo is None
        or now_utc.utcoffset() != timedelta(0)
    ):
        raise ValueError("UTC timestamp required")
    return max(
        now_utc,
        record.created_at_utc,
        record.decided_at_utc or record.created_at_utc,
        record.consumed_at_utc or record.created_at_utc,
    )
