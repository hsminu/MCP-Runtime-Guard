"""I4/I6과 T05의 READ 등급 병합 단위·성질 검증."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from guard_app.core.taint import merge
from guard_app.domain.enums import Label, TaskStatus
from guard_app.domain.models import Escalation, TaskState


def make_state(**updates: object) -> TaskState:
    values: dict[str, object] = {
        "task_id": "task-1",
        "epoch": 3,
        "profile_id": "default",
        "policy_version": "policy-1",
        "status": TaskStatus.RUNNING,
        "security_version": 7,
        "row_revision": 11,
    }
    values.update(updates)
    return TaskState.model_validate(values)


def test_first_confidential_read_records_one_escalation() -> None:
    state = make_state()
    result = merge(state, Label.CONFIDENTIAL, "docs/internal/plan.txt", 4)

    assert result.high_watermark is Label.CONFIDENTIAL
    assert result.last_read_label is Label.CONFIDENTIAL
    assert result.security_version == 8
    assert result.escalations == (
        Escalation(
            at_seq=4,
            source_id="docs/internal/plan.txt",
            from_label=Label.PUBLIC,
            to_label=Label.CONFIDENTIAL,
        ),
    )
    assert result.status is TaskStatus.RUNNING
    assert result.unclassified is False
    assert state.high_watermark is Label.PUBLIC
    assert state.security_version == 7


def test_public_read_cannot_wash_away_confidential_history() -> None:
    """T05/I4: 기밀 → 공개 조회에도 high-watermark와 보안 버전은 유지한다."""
    confidential = merge(make_state(), Label.CONFIDENTIAL, "confidential.txt", 1)
    result = merge(confidential, Label.PUBLIC, "public.txt", 2)

    assert result.high_watermark is Label.CONFIDENTIAL
    assert result.last_read_label is Label.PUBLIC
    assert result.security_version == confidential.security_version
    assert result.escalations == confidential.escalations


def test_rising_reads_preserve_all_escalation_evidence() -> None:
    state = make_state()
    internal = merge(state, Label.INTERNAL, "internal.txt", 1)
    result = merge(internal, Label.CONFIDENTIAL, "confidential.txt", 2)

    assert result.security_version == state.security_version + 2
    assert result.escalations == (
        Escalation(
            at_seq=1,
            source_id="internal.txt",
            from_label=Label.PUBLIC,
            to_label=Label.INTERNAL,
        ),
        Escalation(
            at_seq=2,
            source_id="confidential.txt",
            from_label=Label.INTERNAL,
            to_label=Label.CONFIDENTIAL,
        ),
    )


@pytest.mark.parametrize("label", list(Label))
def test_identical_known_read_returns_original_state(label: Label) -> None:
    state = make_state(high_watermark=label, last_read_label=label)
    assert merge(state, label, "document.txt", 0) is state


def test_classification_failure_halts_and_retains_known_history() -> None:
    """I4/I6: None은 분류 실패이며 무바이트 전송 실패를 뜻하지 않는다."""
    state = merge(make_state(), Label.INTERNAL, "internal.txt", 1)
    result = merge(state, None, "unknown-source", 2)

    assert result.unclassified is True
    assert result.status is TaskStatus.HALTED
    assert result.security_version == state.security_version + 1
    assert result.high_watermark is Label.INTERNAL
    assert result.last_read_label is Label.INTERNAL
    assert result.escalations == state.escalations
    assert state.unclassified is False


def test_repeated_classification_failure_without_security_change_is_idempotent() -> None:
    halted = merge(make_state(), None, "unknown-source", 1)
    assert merge(halted, None, "unknown-source", 2) is halted


@pytest.mark.parametrize(
    ("unclassified", "status"),
    [(False, TaskStatus.HALTED), (True, TaskStatus.RUNNING)],
)
def test_classification_failure_versions_each_actual_security_transition(
    unclassified: bool, status: TaskStatus
) -> None:
    state = make_state(unclassified=unclassified, status=status)
    result = merge(state, None, "unknown-source", 1)
    assert result.unclassified is True
    assert result.status is TaskStatus.HALTED
    assert result.security_version == state.security_version + 1


@pytest.mark.parametrize("label", list(Label))
def test_known_read_never_clears_unclassified_or_resumes_halted_task(label: Label) -> None:
    halted = merge(make_state(), None, "unknown-source", 1)
    result = merge(halted, label, "known-source", 2)
    assert result.unclassified is True
    assert result.status is TaskStatus.HALTED


@pytest.mark.parametrize(
    "status", [TaskStatus.HALTED, TaskStatus.UNKNOWN_OUTCOME, TaskStatus.CLOSED]
)
def test_known_observation_does_not_resume_terminal_task(status: TaskStatus) -> None:
    state = make_state(status=status)
    assert merge(state, Label.CONFIDENTIAL, "document.txt", 1).status is status


def test_store_row_revision_and_task_identity_are_preserved() -> None:
    state = make_state()
    known = merge(state, Label.INTERNAL, "document.txt", 1)
    halted = merge(known, None, "unknown-source", 2)
    for result in (known, halted):
        assert result.row_revision == state.row_revision
        assert result.task_id == state.task_id
        assert result.epoch == state.epoch
        assert result.profile_id == state.profile_id
        assert result.policy_version == state.policy_version


@pytest.mark.parametrize("observed", [False, True, 0, 1, 2, 0.0, "PUBLIC", object()])
def test_non_label_observations_are_rejected_without_coercion(observed: object) -> None:
    state = make_state()
    with pytest.raises(TypeError, match="^READ 관측 등급은 Label 또는 None이어야 한다$"):
        merge(state, observed, "document.txt", 1)
    assert state.high_watermark is Label.PUBLIC


@pytest.mark.parametrize("seq", [True, False, "1", 1.0, None])
def test_sequence_requires_exact_integer(seq: object) -> None:
    with pytest.raises(TypeError, match="^관측 순서는 정수여야 한다$"):
        merge(make_state(), Label.PUBLIC, "document.txt", seq)


def test_sequence_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="^관측 순서는 음수일 수 없다$"):
        merge(make_state(), Label.PUBLIC, "document.txt", -1)


@pytest.mark.parametrize("source_id", [None, False, 1, b"document.txt"])
def test_source_id_requires_string(source_id: object) -> None:
    with pytest.raises(TypeError, match="^관측 출처는 문자열이어야 한다$"):
        merge(make_state(), Label.PUBLIC, source_id, 1)


@pytest.mark.parametrize("source_id", ["", "PRIVATE_SOURCE_MARKER\ud800"])
@pytest.mark.parametrize("observed", [Label.PUBLIC, Label.CONFIDENTIAL, None])
def test_invalid_source_is_rejected_even_when_no_escalation_is_needed(
    source_id: str, observed: Label | None
) -> None:
    with pytest.raises(
        ValueError, match="^관측 출처는 비어 있지 않은 UTF-8 문자열이어야 한다$"
    ) as captured:
        merge(make_state(), observed, source_id, 1)
    assert "PRIVATE_SOURCE_MARKER" not in str(captured.value)


_labels = st.sampled_from(list(Label))
_observations = st.lists(_labels | st.none(), max_size=40)


@settings(max_examples=120, deadline=None)
@given(observations=_observations)
def test_observation_sequences_preserve_monotonicity_and_unclassified(
    observations: list[Label | None],
) -> None:
    """I4/I6: 모든 접두 관측에서 최고 등급은 내려가지 않고 미분류는 고정된다."""
    state = make_state()
    expected_high = Label.PUBLIC
    ever_unclassified = False
    version = state.security_version
    escalations = 0

    for seq, observed in enumerate(observations):
        previous = state
        state = merge(state, observed, "document.txt", seq)
        if observed is None:
            if not ever_unclassified:
                version += 1
            ever_unclassified = True
        elif observed > expected_high:
            version += 1
            escalations += 1
            expected_high = observed

        assert state.high_watermark is expected_high
        assert state.high_watermark >= previous.high_watermark
        assert state.unclassified is ever_unclassified
        assert state.security_version == version
        assert len(state.escalations) == escalations
        assert state.row_revision == 11
        if ever_unclassified:
            assert state.status is TaskStatus.HALTED
        if observed is not None:
            assert state.last_read_label is observed


@settings(max_examples=100, deadline=None)
@given(high=_labels, last=_labels, observed=_labels)
def test_last_read_change_alone_never_changes_security_version(
    high: Label, last: Label, observed: Label
) -> None:
    high = max(high, last, observed)
    state = make_state(high_watermark=high, last_read_label=last)
    result = merge(state, observed, "document.txt", 1)
    assert result.high_watermark is high
    assert result.last_read_label is observed
    assert result.security_version == state.security_version
    assert result.escalations == state.escalations


@settings(max_examples=100, deadline=None)
@given(high=_labels, observed=_labels | st.none(), seq=st.integers(min_value=0, max_value=1000))
def test_merge_is_deterministic_and_does_not_mutate_input(
    high: Label, observed: Label | None, seq: int
) -> None:
    state = make_state(high_watermark=high, last_read_label=high)
    original = state.model_dump_json()
    assert merge(state, observed, "document.txt", seq) == merge(
        state, observed, "document.txt", seq
    )
    assert state.model_dump_json() == original


@settings(max_examples=100, deadline=None)
@given(observations=_observations)
def test_final_security_level_does_not_depend_on_observation_order(
    observations: list[Label | None],
) -> None:
    forward = make_state()
    reverse = make_state()
    for seq, observed in enumerate(observations):
        forward = merge(forward, observed, "document.txt", seq)
    for seq, observed in enumerate(reversed(observations)):
        reverse = merge(reverse, observed, "document.txt", seq)

    assert forward.high_watermark == reverse.high_watermark
    assert forward.unclassified == reverse.unclassified
    assert forward.status == reverse.status
