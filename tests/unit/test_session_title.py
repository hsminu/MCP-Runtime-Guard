"""A188 title privacy and display-only cleaning: I9, T18/T21."""

from datetime import datetime

import pytest

from guard_app.ui.session_title import auto_title, clean_title

NOW = datetime(2026, 10, 30, 9, 7)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("일반 문장", "일반 문장"),
        ("\n  첫 번째 줄\n두 번째 줄", "첫 번째 줄"),
        ("# > - * 1. 제목", "제목"),
        ("```python\nprint('a')", "python"),
        ("제\x00목\u202e  정리", "제목 정리"),
        ("... !!! 😀", "작업 09:07"),
        ("\n \n", "작업 09:07"),
        (
            "이 문장은 세션 제목의 글자 수 제한을 확인하기 위한 긴 문장임",
            "이 문장은 세션 제목의 글자 수 제한을 확인하기…",
        ),
        ("가" * 40, "가" * 27 + "…"),
        # Token prefixes count only at a word start, not inside ordinary words.
        ("task-demo 결과 정리", "task-demo 결과 정리"),
        ("work_dir와 desk-top 정리", "work_dir와 desk-top 정리"),
    ],
)
def test_auto_title(text, expected):
    assert auto_title(text, NOW) == expected
    assert len(auto_title(text, NOW)) <= 28


@pytest.mark.parametrize(
    "secret",
    [
        "sk-value",
        "sk_value",
        "rk_value",
        "pk_value",
        "ghp_value",
        "gho_value",
        "ghu_value",
        "ghs_value",
        "github_pat_value",
        "glpat-value",
        "xoxa-value",
        "xoxb-value",
        "xoxp-value",
        "xoxr-value",
        "xoxs-value",
        "AKIA123456789ABC",
        "AIza" + "a" * 20,
        "eyJabcdefghijk.payload",
        "-----BEGIN PRIVATE KEY-----",
        *[
            f"{name}: value"
            for name in (
                "password",
                "passwd",
                "pwd",
                "secret",
                "token",
                "api_key",
                "api-key",
                "apikey",
                "access_key",
                "private-key",
                "client_secret",
                "authorization",
                "bearer",
                "비밀번호",
                "암호",
                "토큰",
                "인증키",
                "api 키",
            )
        ],
        "Bearer arbitrary",
        "PASSWORD = value",
        "abcDEF123_" * 3,
        "person@example.com",
        "123456789",
        "010-1234-5678",
        "123 456 789",
        "https://person:pass@example.test/",
        "s\u200bk-value",
    ],
)
def test_sensitive_anywhere_uses_generic_title(secret):
    assert auto_title("안전한 첫 줄\n" + secret, NOW) == "작업 09:07"


def test_clean_title():
    assert clean_title("  제\x00목\u200b\u202e   정리  ") == "제목 정리"
    assert clean_title("x" * 90) == "x" * 80
    assert clean_title("\x00\u200b  ") == ""
