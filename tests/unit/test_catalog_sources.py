"""B3 출처 해석의 I6/I7과 T06/T38 단위 검증.

순수 lexical 검증이며 실제 경로·symlink 안전성이나 응답 커밋은 검증하지 않는다.
"""

from dataclasses import FrozenInstanceError

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from guard_app.domain.enums import Label
from guard_app.services.catalog_sources import (
    SourceClassifier,
    SourceConfigError,
    SourceRule,
    canonical_resource_parts,
    valid_server_id,
)


def source(
    rule_prefix: tuple[str, ...], label: Label = Label.INTERNAL, **updates: object
) -> SourceRule:
    values: dict[str, object] = {
        "server_id": "docs",
        "tool_name": "read_document",
        "prefix": rule_prefix,
        "label": label,
    }
    values.update(updates)
    return SourceRule.model_validate(values)


def test_source_configuration_rejects_invalid_prefix_segments(subtests) -> None:
    """I6: 설정을 조용히 정규화하거나 민감 경로 규칙을 추측하지 않는다."""
    for case_index, segment in enumerate(
        ["", ".", "..", "a/b", "a\\b", "\x00", "\n", "\x7f", "\x85", "\u202e", "\ud800"]
    ):
        with subtests.test(case=case_index), pytest.raises(ValidationError):
            source((segment,))


def test_source_configuration_requires_strict_immutable_segments(subtests) -> None:
    for case_index, prefix in enumerate([["company"], (1,), (None,), "company"]):
        with subtests.test(case=case_index), pytest.raises(ValidationError):
            source((), prefix=prefix)


def test_server_identifier_rejects_ambiguous_namespace_or_control_values(subtests) -> None:
    for case_index, server_id in enumerate(
        ["", "docs__remote", "docs_", "docs\x00", "docs\n", "docs\u202e", "\udfff", None, 1]
    ):
        with subtests.test(case=case_index):
            assert valid_server_id(server_id) is False
            with pytest.raises(ValidationError):
                source((), server_id=server_id)


def test_server_identifier_preserves_valid_exact_names(subtests) -> None:
    for case_index, server_id in enumerate(["docs", "local_docs", "회사", "CaseSensitive"]):
        with subtests.test(case=case_index):
            assert valid_server_id(server_id) is True
            assert source((), server_id=server_id).server_id == server_id


def test_source_configuration_rejects_invalid_tool_identity(subtests) -> None:
    for case_index, tool_name in enumerate(["", "read\x00", "read\n", "\ud800", True]):
        with subtests.test(case=case_index), pytest.raises(ValidationError):
            source((), tool_name=tool_name)


def test_resource_id_requires_canonical_relative_posix_segments(subtests) -> None:
    for case_index, resource_id in enumerate(
        [
            None,
            False,
            1,
            ["company"],
            "/company/plan",
            "//company/plan",
            "company\\plan",
            "company//plan",
            "./company/plan",
            "company/./plan",
            "company/../plan",
            "../company/plan",
            "company/plan/",
            "company/\x00plan",
            "company/\nplan",
            "company/\u202eplan",
            "company/\ud800",
        ]
    ):
        with subtests.test(case=case_index):
            assert canonical_resource_parts(resource_id) is None
            classifier = SourceClassifier((source((), Label.PUBLIC),))
            assert classifier.classify("docs", "read_document", resource_id) is None


def test_canonical_resource_parser_preserves_exact_text(subtests) -> None:
    for case_index, (resource_id, parts) in enumerate(
        [
            ("", ()),
            ("company", ("company",)),
            ("company/plan.md", ("company", "plan.md")),
            (" company /plan. ", (" company ", "plan. ")),
            ("회사/계획", ("회사", "계획")),
            ("é/é", ("é", "é")),
        ]
    ):
        with subtests.test(case=case_index):
            assert canonical_resource_parts(resource_id) == parts


def test_prefix_match_uses_segments_and_exact_server_and_tool() -> None:
    """T06/T38: 서버·도구·경로를 도구가 주장한 label 대신 등록 규칙으로 판정한다."""
    classifier = SourceClassifier((source(("company",)),))
    assert classifier.classify("docs", "read_document", "company/plan.md") is Label.INTERNAL
    for server_id, tool_name, resource_id in (
        ("docs", "read_document", "companyX/plan.md"),
        ("remote", "read_document", "company/plan.md"),
        ("docs", "read_summary", "company/plan.md"),
        ("Docs", "read_document", "company/plan.md"),
    ):
        assert classifier.classify(server_id, tool_name, resource_id) is None


def test_nested_public_rule_cannot_downgrade_confidential_parent() -> None:
    classifier = SourceClassifier(
        (source(("secret",), Label.CONFIDENTIAL), source(("secret", "public"), Label.PUBLIC))
    )
    assert (
        classifier.classify("docs", "read_document", "secret/public/notice") is Label.CONFIDENTIAL
    )


def test_empty_prefix_overlaps_resource_rules_and_takes_maximum() -> None:
    classifier = SourceClassifier(
        (source((), Label.CONFIDENTIAL), source(("company",), Label.PUBLIC))
    )
    assert classifier.classify("docs", "read_document", "company/plan") is Label.CONFIDENTIAL


def test_tool_level_read_matches_only_empty_prefix() -> None:
    assert SourceClassifier((source(("company",)),)).classify("docs", "read_document", "") is None
    classifier = SourceClassifier(
        (source((), Label.PUBLIC), source(("secret",), Label.CONFIDENTIAL))
    )
    assert classifier.classify("docs", "read_document", "") is Label.PUBLIC


def test_dynamic_invalid_identity_is_unclassified(subtests) -> None:
    for case_index, (server_id, tool_name) in enumerate(
        [(None, "read_document"), ("docs", None), ("docs_", "read_document"), ("docs", "read\x00")]
    ):
        with subtests.test(case=case_index):
            classifier = SourceClassifier((source((), Label.PUBLIC),))
            assert classifier.classify(server_id, tool_name, "company/plan") is None


def test_rule_and_classifier_cannot_be_changed_after_creation() -> None:
    rule = source(("company",))
    classifier = SourceClassifier((rule,))
    with pytest.raises(ValidationError):
        rule.prefix = ()
    with pytest.raises(FrozenInstanceError):
        classifier.rules = ()
    with pytest.raises(ValidationError):
        rule.model_copy(update={"prefix": ("..",)})
    assert classifier.classify("docs", "read_document", "company/plan") is Label.INTERNAL


@pytest.mark.parametrize("rules", [[source(())], (None,), ({"prefix": []},), None])
def test_classifier_requires_validated_rule_tuple(rules: object) -> None:
    with pytest.raises(SourceConfigError):
        SourceClassifier(rules)


def test_classifier_revalidates_constructed_rule_before_using_it() -> None:
    forged = SourceRule.model_construct(
        server_id="docs", tool_name="read_document", prefix=("..",), label=Label.PUBLIC
    )
    with pytest.raises(ValidationError):
        SourceClassifier((forged,))


def test_source_rule_json_roundtrip_and_security_label_validation() -> None:
    rule = source(("company", "계획"), Label.CONFIDENTIAL)
    assert SourceRule.model_validate_json(rule.model_dump_json()) == rule
    for value in ("true", "1.0", '"1"'):
        with pytest.raises(ValidationError):
            SourceRule.model_validate_json(
                '{"server_id":"docs","tool_name":"read_document","prefix":[],"label":' + value + "}"
            )


_segment = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
)
_labels = st.sampled_from(tuple(Label))


@settings(max_examples=60)
@given(_segment, _labels)
def test_prefix_collisions_are_always_unclassified(segment: str, label: Label) -> None:
    classifier = SourceClassifier((source((segment,), label),))
    assert classifier.classify("docs", "read_document", segment + "X/plan") is None


@settings(max_examples=60)
@given(st.lists(_labels, min_size=1, max_size=8), st.permutations(range(3)))
def test_matched_rules_choose_maximum_independently_of_order(
    labels: list[Label], order: list[int]
) -> None:
    rules = tuple(source(("company",), label) for label in labels)
    expected = max(labels)
    assert SourceClassifier(rules).classify("docs", "read_document", "company/plan") is expected
    assert (
        SourceClassifier(tuple(reversed(rules))).classify("docs", "read_document", "company/plan")
        is expected
    )
    nested = (
        source((), labels[0]),
        source(("company",), labels[-1]),
        source(("company", "plan"), expected),
    )
    assert (
        SourceClassifier(tuple(nested[index] for index in order)).classify(
            "docs", "read_document", "company/plan"
        )
        is expected
    )


@settings(max_examples=60)
@given(_segment, _segment, _labels)
def test_unknown_identity_is_never_guessed_public(server: str, tool: str, label: Label) -> None:
    classifier = SourceClassifier((source((), label, server_id=server, tool_name=tool),))
    assert classifier.classify(server + "X", tool, "plan") is None
    assert classifier.classify(server, tool + "X", "plan") is None


@settings(max_examples=60)
@given(_segment, _labels)
def test_mutable_json_copy_cannot_change_classifier_snapshot(segment: str, label: Label) -> None:
    rule = source((segment,), label)
    classifier = SourceClassifier((rule,))
    mutable_copy = rule.model_dump(mode="json")
    mutable_copy["prefix"].clear()
    mutable_copy["label"] = int(Label.PUBLIC)
    assert classifier.classify("docs", "read_document", segment + "/plan") is label
    assert classifier.rules[0].prefix == (segment,)
