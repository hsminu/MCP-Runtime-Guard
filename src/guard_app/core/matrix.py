"""기술설계서 5.1절: 수신 등급 상한을 먼저 적용하는 경계 판정."""

from types import MappingProxyType

from guard_app.domain.enums import Decision, Label, TrustTier
from guard_app.domain.models import Boundary, BoundaryVerdict
from guard_app.domain.reasons import Reason

_BASE = MappingProxyType(
    {
        (Label.PUBLIC, TrustTier.INTERNAL): Decision.ALLOW,
        (Label.INTERNAL, TrustTier.INTERNAL): Decision.ALLOW,
        (Label.CONFIDENTIAL, TrustTier.INTERNAL): Decision.ALLOW,
        (Label.PUBLIC, TrustTier.APPROVED_EXTERNAL): Decision.ALLOW,
        (Label.INTERNAL, TrustTier.APPROVED_EXTERNAL): Decision.ASK,
        (Label.CONFIDENTIAL, TrustTier.APPROVED_EXTERNAL): Decision.DENY,
        (Label.PUBLIC, TrustTier.GENERAL_EXTERNAL): Decision.ALLOW,
        (Label.INTERNAL, TrustTier.GENERAL_EXTERNAL): Decision.ASK,
        (Label.CONFIDENTIAL, TrustTier.GENERAL_EXTERNAL): Decision.DENY,
    }
)
_REASONS = MappingProxyType(
    {
        Decision.ALLOW: Reason.OK,
        Decision.ASK: Reason.INTERNAL_TO_EXTERNAL,
        Decision.DENY: Reason.CONFIDENTIAL_TO_EXTERNAL,
    }
)


def judge_boundary(label: Label, boundary: Boundary) -> BoundaryVerdict:
    """접속 방식이나 이름을 사용하지 않고 등록된 경계만 판정한다."""
    if type(label) is not Label:
        raise TypeError("경계 판정에는 Label 인스턴스가 필요하다")
    if label > boundary.max_label:
        return BoundaryVerdict(
            boundary_id=boundary.boundary_id,
            decision=Decision.DENY,
            reason=Reason.LABEL_EXCEEDS_BOUNDARY,
        )
    decision = _BASE[(label, boundary.tier)]
    return BoundaryVerdict(
        boundary_id=boundary.boundary_id, decision=decision, reason=_REASONS[decision]
    )
