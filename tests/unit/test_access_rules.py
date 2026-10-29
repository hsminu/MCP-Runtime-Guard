"""ACCESS-01 1단계·I2/I6: 정확 일치·기본 거부·거부 우선의 순수 검증.

Guard 연결 뒤의 dispatch == 0 검증은 C의 독립 인수 대상이다.
"""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from itertools import product

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from guard_app.core.access_rules import (
    AccessRules,
    AccessRulesError,
    evaluate_access,
    normalize_access_rules,
)
from guard_app.domain.models import AccessResult


def test_empty_rules_deny_by_default() -> None:
    result = evaluate_access(AccessRules(), server_id="server", tool_name="tool")
    assert result == AccessResult(allowed=False, reason="ACCESS_RULE_MISSING", error=False)


def test_allow_and_explicit_deny_return_existing_domain_results() -> None:
    rules = AccessRules(allow=[("server", "read")], deny=[("server", "send")])
    assert rules.is_valid
    assert evaluate_access(rules, server_id="server", tool_name="read") == AccessResult(
        allowed=True, reason="ACCESS_RULE_ALLOW", error=False
    )
    assert evaluate_access(rules, server_id="server", tool_name="send") == AccessResult(
        allowed=False, reason="ACCESS_RULE_DENY", error=False
    )


def test_deny_wins_when_both_lists_contain_the_pair() -> None:
    pair = ("server", "read")
    result = evaluate_access(
        AccessRules(allow=[pair], deny=[pair]), server_id="server", tool_name="read"
    )
    assert result == AccessResult(allowed=False, reason="ACCESS_RULE_DENY", error=False)


def test_case_prefix_whitespace_and_server_names_are_not_normalized(subtests) -> None:
    for case_index, ((server_id, tool_name),) in enumerate(
        product(
            [
                ("SERVER", "Read"),
                ("server", "read"),
                ("server-extra", "Read"),
                ("server", "ReadExtra"),
                ("server ", "Read"),
                ("server", "Read "),
                ("other", "Read"),
            ]
        )
    ):
        with subtests.test(case=case_index):
            result = evaluate_access(
                AccessRules(allow=[("server", "Read")]), server_id=server_id, tool_name=tool_name
            )
            assert not result.allowed
            assert not result.error


def test_wildcards_and_expression_shaped_names_are_only_literal_names() -> None:
    rules = AccessRules(allow=[("*", "read*"), ("server", "read || allow()")])
    assert not evaluate_access(rules, server_id="server", tool_name="read").allowed
    assert evaluate_access(rules, server_id="*", tool_name="read*").allowed
    assert evaluate_access(rules, server_id="server", tool_name="read || allow()").allowed


def test_unicode_and_delimiter_shaped_names_remain_distinct() -> None:
    rules = AccessRules(allow=[("é", "read"), ("a__b", "c")])
    assert evaluate_access(rules, server_id="é", tool_name="read").allowed
    assert not evaluate_access(rules, server_id="e\u0301", tool_name="read").allowed
    assert not evaluate_access(rules, server_id="a", tool_name="b__c").allowed


def test_supported_containers_are_copied_into_equivalent_rules(subtests) -> None:
    for case_index, (pairs,) in enumerate(
        product(
            [
                [("server", "read")],
                (("server", "read"),),
                {("server", "read")},
                frozenset({("server", "read")}),
                [["server", "read"]],
            ]
        )
    ):
        with subtests.test(case=case_index):
            assert AccessRules(allow=pairs) == AccessRules(allow=[("server", "read")])


class _String(str):
    pass


def test_malformed_rules_in_either_list_invalidate_the_entire_set(subtests) -> None:
    for case_index, (side, malformed) in enumerate(
        product(
            ["allow", "deny"],
            [
                None,
                True,
                "RAW_RULE",
                b"RAW_RULE",
                {"server": "tool"},
                ["server", "tool"],
                [("server",)],
                [("server", "tool", "allow")],
                [{"server_id": "server", "tool_name": "tool", "decision": "allow"}],
                [(1, "tool")],
                [("server", False)],
                [("", "tool")],
                [("server", "")],
                [("server\ud800", "tool")],
                [("server", "tool\udfff")],
                [(_String("server"), "tool")],
                [("server", "read"), ("partial-invalid",)],
            ],
        )
    ):
        with subtests.test(case=case_index):
            inputs: dict[str, object] = {"allow": [("server", "read")], "deny": []}
            inputs[side] = deepcopy(malformed)
            rules = AccessRules(**inputs)
            assert not rules.is_valid
            for server_id, tool_name in [("server", "read"), ("other", "tool")]:
                result = evaluate_access(rules, server_id=server_id, tool_name=tool_name)
                assert result == AccessResult(
                    allowed=False, reason="ACCESS_RULES_INVALID", error=True
                )
                assert "RAW_RULE" not in str(result)
            with pytest.raises(AccessRulesError) as caught:
                normalize_access_rules(rules)
            assert "RAW_RULE" not in str(caught.value)


def test_unvalidated_rules_objects_cannot_grant_access(subtests) -> None:
    for case_index, (rules,) in enumerate(
        product([None, {}, [], (), frozenset(), "RAW_RULE", True])
    ):
        with subtests.test(case=case_index):
            assert evaluate_access(rules, server_id="server", tool_name="tool") == AccessResult(
                allowed=False, reason="ACCESS_RULES_INVALID", error=True
            )
            with pytest.raises(AccessRulesError):
                normalize_access_rules(rules)


def test_malformed_request_names_are_fixed_errors(subtests) -> None:
    for case_index, (field, name) in enumerate(
        product(
            ["server_id", "tool_name"], [None, 1, True, [], "", "RAW_NAME\ud800", _String("server")]
        )
    ):
        with subtests.test(case=case_index):
            request: dict[str, object] = {"server_id": "server", "tool_name": "tool"}
            request[field] = deepcopy(name)
            result = evaluate_access(AccessRules(allow=[("server", "tool")]), **request)
            assert result == AccessResult(
                allowed=False, reason="ACCESS_REQUEST_INVALID", error=True
            )
            assert "RAW_NAME" not in str(result)


def test_untrusted_iterators_are_not_executed() -> None:
    class UnexpectedIterator:
        def __iter__(self):
            pytest.fail("arbitrary iterators must not be executed")

    rules = AccessRules(allow=UnexpectedIterator())
    assert not rules.is_valid
    assert evaluate_access(rules, server_id="server", tool_name="read").error


def test_rules_are_immutable_and_do_not_retain_mutable_input_or_raw_repr() -> None:
    pair = ["RAW_SERVER", "RAW_TOOL"]
    allow = [pair]
    deny: set[tuple[str, str]] = set()
    rules = AccessRules(allow=allow, deny=deny)
    pair[0] = "changed"
    allow.append(["new", "tool"])
    deny.add(("RAW_SERVER", "RAW_TOOL"))
    assert evaluate_access(rules, server_id="RAW_SERVER", tool_name="RAW_TOOL").allowed
    assert not evaluate_access(rules, server_id="new", tool_name="tool").allowed
    with pytest.raises(FrozenInstanceError):
        rules._allow = frozenset()  # type: ignore[misc]
    assert repr(rules) == "AccessRules(<redacted>)"
    assert "RAW_SERVER" not in repr(rules)
    assert "RAW_TOOL" not in repr(rules)


def test_normalization_sorts_deduplicates_and_preserves_deny_overlap() -> None:
    rules = AccessRules(
        allow=[("z", "read"), ("a", "z"), ("a", "a"), ("z", "read")],
        deny=[("z", "read"), ("a", "a")],
    )
    expected = {
        "allow": [["a", "a"], ["a", "z"], ["z", "read"]],
        "deny": [["a", "a"], ["z", "read"]],
    }
    assert normalize_access_rules(rules) == expected
    returned = normalize_access_rules(rules)
    returned["allow"][0][0] = "changed"
    returned["deny"].clear()
    assert normalize_access_rules(rules) == expected


_name = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=10)
_pair = st.tuples(_name, _name)
_pairs = st.sets(_pair, max_size=15)


@settings(deadline=None)
@given(allow=_pairs, deny=_pairs, request=_pair)
def test_decision_and_normalization_are_deterministic(
    allow: set[tuple[str, str]], deny: set[tuple[str, str]], request: tuple[str, str]
) -> None:
    rules = AccessRules(allow=allow, deny=deny)
    reordered = AccessRules(allow=list(reversed(sorted(allow))) * 2, deny=sorted(deny) * 2)
    assert rules == reordered
    assert normalize_access_rules(rules) == normalize_access_rules(reordered)
    first = evaluate_access(rules, server_id=request[0], tool_name=request[1])
    assert first == evaluate_access(rules, server_id=request[0], tool_name=request[1])
    assert first == evaluate_access(reordered, server_id=request[0], tool_name=request[1])
    assert first.allowed == (request in allow and request not in deny)
    assert not first.error


@settings(deadline=None)
@given(request=_pair)
def test_default_deny_property(request: tuple[str, str]) -> None:
    result = evaluate_access(AccessRules(), server_id=request[0], tool_name=request[1])
    assert not result.allowed
    assert not result.error


@settings(deadline=None)
@given(allow=_pairs, deny=_pairs, request=_pair)
def test_adding_an_explicit_deny_always_prevents_allow(
    allow: set[tuple[str, str]], deny: set[tuple[str, str]], request: tuple[str, str]
) -> None:
    rules = AccessRules(allow=allow | {request}, deny=deny | {request})
    result = evaluate_access(rules, server_id=request[0], tool_name=request[1])
    assert not result.allowed
    assert not result.error
    assert result.reason == "ACCESS_RULE_DENY"


@settings(deadline=None)
@given(allow=_pairs, deny=_pairs, request=_pair)
def test_pairs_outside_both_lists_are_denied(
    allow: set[tuple[str, str]], deny: set[tuple[str, str]], request: tuple[str, str]
) -> None:
    assume(request not in allow | deny)
    result = evaluate_access(
        AccessRules(allow=allow, deny=deny), server_id=request[0], tool_name=request[1]
    )
    assert not result.allowed
    assert not result.error


@settings(deadline=None)
@given(request=_pair)
def test_the_same_tool_on_a_different_server_is_not_allowed(request: tuple[str, str]) -> None:
    rules = AccessRules(allow=[request])
    assert evaluate_access(rules, server_id=request[0], tool_name=request[1]).allowed
    other = evaluate_access(rules, server_id=request[0] + "-other", tool_name=request[1])
    assert not other.allowed
    assert not other.error


@settings(deadline=None)
@given(allow=_pairs, deny=_pairs)
def test_normalized_representation_roundtrips_without_changing_rules(
    allow: set[tuple[str, str]], deny: set[tuple[str, str]]
) -> None:
    rules = AccessRules(allow=allow, deny=deny)
    assert AccessRules(**normalize_access_rules(rules)) == rules
