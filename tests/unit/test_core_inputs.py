"""I6: 순수 함수도 정수·bool을 등급으로 묵시 변환하지 않는다."""

from typing import cast

import pytest

from guard_app.core.matrix import judge_boundary
from guard_app.core.policy import PolicyConfigError, effective_label
from guard_app.domain.enums import Label, TrustTier
from guard_app.domain.models import Boundary, TaskState


@pytest.mark.parametrize("label", [False, True, 0, 1, 2, 1.0, "INTERNAL", None])
def test_boundary_requires_label_enum(label: object) -> None:
    boundary = Boundary(
        boundary_id="internal", tier=TrustTier.INTERNAL, max_label=Label.CONFIDENTIAL
    )
    with pytest.raises(TypeError, match="Label"):
        judge_boundary(cast(Label, label), boundary)


def test_unknown_mode_error_does_not_echo_config_value() -> None:
    state = TaskState(task_id="task", epoch=0, profile_id="default", policy_version="v1")
    unknown_mode = "synthetic-sensitive-mode"
    with pytest.raises(PolicyConfigError) as caught:
        effective_label(state, unknown_mode)
    assert unknown_mode not in str(caught.value)
    assert unknown_mode not in repr(caught.value)
