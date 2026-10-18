"""B5.11·I5/I9: 모델 반환의 성공 표시·고정 문구·내부 정보 비공개 검증."""

from itertools import permutations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from guard_app.core import model_messages
from guard_app.core.model_messages import (
    MODEL_REASON_MESSAGES,
    MODEL_VISIBLE_REASONS,
    ModelMessageError,
    ModelMessageKind,
    fixed_model_message,
    outcome_model_message,
)
from guard_app.domain.contracts import (
    Denied,
    OutcomeUnknown,
    Pending,
    Released,
    ReleasedResult,
    Withheld,
)
from guard_app.domain.enums import Label
from guard_app.domain.reasons import Reason

_DENIED_TEXT = "이 요청은 정책에 의해 차단되었습니다"
_STOPPED_TEXT = "작업이 정지되었습니다"
_VISIBLE_REASONS = {
    Reason.INTERNAL_TO_EXTERNAL,
    Reason.CONFIDENTIAL_TO_EXTERNAL,
    Reason.INVALID_ARGUMENTS,
    Reason.TOOL_UNSUPPORTED,
}


def test_every_reason_has_an_explicit_message_and_visibility_is_an_allowlist() -> None:
    # 새 Reason은 문구·공개 여부를 명시적으로 검토하지 않으면 검사를 통과하지 못한다.
    assert set(MODEL_REASON_MESSAGES) == set(Reason)
    assert MODEL_VISIBLE_REASONS == _VISIBLE_REASONS
    assert set(model_messages._PUBLIC_REASON_PRIORITY) == MODEL_VISIBLE_REASONS


@pytest.mark.parametrize("reason", list(Reason))
def test_each_reason_has_a_fixed_denial_message(reason: Reason) -> None:
    success, text = fixed_model_message(ModelMessageKind.DENIED, reason)
    assert success is False
    assert text == MODEL_REASON_MESSAGES[reason]
    if reason in _VISIBLE_REASONS:
        assert text == f"{_DENIED_TEXT}: {reason.value}"
    else:
        assert text == _DENIED_TEXT
        assert reason.value not in text


@pytest.mark.parametrize("reason", list(Reason))
def test_denied_outcomes_cover_every_reason_without_metadata(reason: Reason) -> None:
    outcome = Denied(request_id="PRIVATE_REQUEST", reasons=(reason,))
    assert outcome_model_message(outcome) == fixed_model_message(ModelMessageKind.DENIED, reason)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ModelMessageKind.RELEASED, (True, "")),
        (ModelMessageKind.DENIED, (False, _DENIED_TEXT)),
        (ModelMessageKind.WITHHELD, (False, _STOPPED_TEXT)),
        (ModelMessageKind.OUTCOME_UNKNOWN, (False, _STOPPED_TEXT)),
        (ModelMessageKind.APPROVAL_REJECTED, (False, "승인 요청이 거절되었습니다")),
        (ModelMessageKind.APPROVAL_EXPIRED, (False, "승인 요청이 만료되었습니다")),
        (ModelMessageKind.APPROVAL_CANCELLED, (False, "요청이 취소되었습니다")),
        (ModelMessageKind.APPROVAL_INVALIDATED, (False, "승인 요청이 무효화되었습니다")),
        (ModelMessageKind.NAMESPACE_MISSING, (False, _DENIED_TEXT)),
        (ModelMessageKind.STOPPED, (False, _STOPPED_TEXT)),
    ],
)
def test_final_message_kinds_have_fixed_results(
    kind: ModelMessageKind, expected: tuple[bool, str]
) -> None:
    assert fixed_model_message(kind) == expected


def test_kind_table_covers_all_final_kinds_and_only_released_is_successful() -> None:
    final_kinds = set(ModelMessageKind) - {ModelMessageKind.PENDING}
    assert set(model_messages._KIND_MESSAGES) | {ModelMessageKind.DENIED} == final_kinds
    for kind in final_kinds:
        success, _ = fixed_model_message(kind)
        assert success is (kind is ModelMessageKind.RELEASED)
    assert fixed_model_message(ModelMessageKind.DENIED, Reason.OK)[0] is False


@pytest.mark.parametrize("reason", list(Reason))
@pytest.mark.parametrize(
    "kind", [ModelMessageKind.WITHHELD, ModelMessageKind.OUTCOME_UNKNOWN, ModelMessageKind.STOPPED]
)
def test_all_stop_results_hide_all_reasons(kind: ModelMessageKind, reason: Reason) -> None:
    assert fixed_model_message(kind, reason) == (False, _STOPPED_TEXT)


def test_expiry_rejection_cancellation_and_stop_are_distinct_extension_points() -> None:
    kinds = (
        ModelMessageKind.APPROVAL_REJECTED,
        ModelMessageKind.APPROVAL_EXPIRED,
        ModelMessageKind.APPROVAL_CANCELLED,
        ModelMessageKind.STOPPED,
    )
    assert len({fixed_model_message(kind)[1] for kind in kinds}) == len(kinds)
    # 기존 APPROVAL_STALE만으로 승인 종결 종류를 추측하지 않는다.
    denied = Denied(request_id="PRIVATE_REQUEST", reasons=(Reason.APPROVAL_STALE,))
    assert outcome_model_message(denied) == (False, _DENIED_TEXT)


def test_pending_never_produces_a_terminal_response() -> None:
    with pytest.raises(ModelMessageError, match="승인 대기"):
        fixed_model_message(ModelMessageKind.PENDING)
    pending = Pending(
        request_id="PRIVATE_REQUEST",
        approval_id="PRIVATE_APPROVAL",
        reasons=(Reason.INTERNAL_TO_EXTERNAL,),
    )
    with pytest.raises(ModelMessageError, match="승인 대기") as caught:
        outcome_model_message(pending)
    assert "PRIVATE_" not in str(caught.value)


def test_released_body_is_not_read_or_serialized() -> None:
    class UnreadableBody:
        def __getattribute__(self, name: str) -> object:
            pytest.fail("released body must not be inspected")

        def __repr__(self) -> str:
            pytest.fail("released body must not be represented")

    outcome = Released.model_construct(
        request_id="PRIVATE_REQUEST", result=UnreadableBody(), label=Label.CONFIDENTIAL
    )
    assert outcome_model_message(outcome) == (True, "")


@pytest.mark.parametrize("reason", list(Reason))
def test_released_does_not_accept_contradictory_reason_metadata(reason: Reason) -> None:
    with pytest.raises(ModelMessageError):
        fixed_model_message(ModelMessageKind.RELEASED, reason)


def test_multiple_public_reasons_have_stable_priority_independent_of_order_and_duplicates() -> None:
    expected = fixed_model_message(ModelMessageKind.DENIED, Reason.CONFIDENTIAL_TO_EXTERNAL)
    for ordered in permutations(_VISIBLE_REASONS):
        outcome = Denied(request_id="PRIVATE_REQUEST", reasons=ordered + ordered)
        assert outcome_model_message(outcome) == expected


@pytest.mark.parametrize("private_reason", sorted(set(Reason) - _VISIBLE_REASONS))
def test_private_reason_masks_other_reasons(private_reason: Reason) -> None:
    reasons = tuple(_VISIBLE_REASONS) + (private_reason,)
    assert outcome_model_message(Denied(request_id=None, reasons=reasons)) == (False, _DENIED_TEXT)
    assert outcome_model_message(Denied(request_id=None, reasons=tuple(reversed(reasons)))) == (
        False,
        _DENIED_TEXT,
    )


def test_reason_table_is_immutable() -> None:
    with pytest.raises(TypeError):
        MODEL_REASON_MESSAGES[Reason.OK] = "UNSAFE"  # type: ignore[index]


def test_missing_reason_mapping_fails_instead_of_using_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = {key: value for key, value in MODEL_REASON_MESSAGES.items() if key is not Reason.OK}
    monkeypatch.setattr(model_messages, "MODEL_REASON_MESSAGES", missing)
    with pytest.raises(ModelMessageError, match="정의되지 않았다"):
        fixed_model_message(ModelMessageKind.DENIED, Reason.OK)
    with pytest.raises(ModelMessageError, match="정의되지 않았다"):
        outcome_model_message(Denied(request_id=None, reasons=(Reason.OK,)))


@pytest.mark.parametrize("kind", [None, "released", "PRIVATE_KIND", {}, True])
def test_unknown_or_untyped_kinds_are_fixed_errors(kind: object) -> None:
    with pytest.raises(ModelMessageError) as caught:
        fixed_model_message(kind)  # type: ignore[arg-type]
    assert "PRIVATE_" not in str(caught.value)


@pytest.mark.parametrize("reason", ["OK", "PRIVATE_REASON", {}, True, ValueError("PRIVATE_RAW")])
def test_raw_reason_strings_exceptions_and_objects_are_not_interpolated(reason: object) -> None:
    with pytest.raises(ModelMessageError) as caught:
        fixed_model_message(ModelMessageKind.DENIED, reason)  # type: ignore[arg-type]
    assert "PRIVATE_" not in str(caught.value)
    assert "PRIVATE_" not in repr(caught.value)


@pytest.mark.parametrize("outcome", [None, "PRIVATE_BODY", {"kind": "released"}, True])
def test_unvalidated_outcomes_are_fixed_errors(outcome: object) -> None:
    with pytest.raises(ModelMessageError) as caught:
        outcome_model_message(outcome)
    assert "PRIVATE_" not in str(caught.value)


@pytest.mark.parametrize("reasons", [(), [], "PRIVATE_REASON", ("PRIVATE_REASON",)])
def test_unvalidated_denial_reasons_are_fixed_errors(reasons: object) -> None:
    outcome = Denied.model_construct(request_id="PRIVATE_REQUEST", reasons=reasons)
    with pytest.raises(ModelMessageError) as caught:
        outcome_model_message(outcome)
    assert "PRIVATE_" not in str(caught.value)


_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=30)


@given(text=_text, reason=st.sampled_from(list(Reason)))
def test_identifiers_versions_arguments_and_original_content_do_not_enter_messages(
    text: str, reason: Reason
) -> None:
    marker = "PRIVATE_MARKER_" + text
    result = ReleasedResult(
        text=marker,
        structured_content={
            "boundary_id": marker,
            "policy_version": marker,
            "security_version": marker,
            "arguments": {"document": marker},
        },
    )
    outcomes = (
        Released(request_id=marker, result=result, label=Label.CONFIDENTIAL),
        Denied(request_id=marker, reasons=(reason,)),
        Withheld(request_id=marker, reasons=(reason,)),
        OutcomeUnknown(request_id=marker, reasons=(reason,)),
    )
    for outcome in outcomes:
        first = outcome_model_message(outcome)
        assert first == outcome_model_message(outcome)
        assert marker not in first[1]
        assert "PRIVATE_MARKER_" not in repr(first)


@given(kind=st.sampled_from(list(ModelMessageKind)), reason=st.sampled_from(list(Reason)))
def test_fixed_message_determinism(kind: ModelMessageKind, reason: Reason) -> None:
    if kind in (ModelMessageKind.PENDING, ModelMessageKind.RELEASED):
        with pytest.raises(ModelMessageError):
            fixed_model_message(kind, reason)
    else:
        assert fixed_model_message(kind, reason) == fixed_model_message(kind, reason)
