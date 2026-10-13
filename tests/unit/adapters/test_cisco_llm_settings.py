"""I6·I9 / T17·T19: Scanner 설정과 키 참조의 실패·비노출 검증."""

import os

import pytest

from guard_app.adapters.cisco_llm import ScannerLLMError, ScannerLLMSettings
from guard_app.security.secrets import (
    SecretLoadError,
    load_scanner_environment,
    scanner_sdk_import_environment,
)


def test_env_file_alias_uses_explicit_process_override_without_mutating_environment(tmp_path):
    file = tmp_path / "scanner.env"
    file.write_text(
        "OPENAI_API_KEY=file_test_key\n"
        "MCP_SCANNER_LLM_API_KEY=${OPENAI_API_KEY}\n"
        "MCP_SCANNER_LLM_MODEL=openai/gpt-6-luna\n"
        "MCP_SCANNER_LLM_REASONING_EFFORT=low\n"
        "MCP_SCANNER_LLM_TIMEOUT=12\n"
        "MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS=2048\n",
        encoding="utf-8",
    )
    before = dict(os.environ)
    settings = ScannerLLMSettings.load(file, environ={"OPENAI_API_KEY": "override_test_key"})
    assert settings.api_key == "override_test_key"
    assert settings.timeout_seconds == 12.0
    assert settings.max_completion_tokens == 2048
    assert settings.model == "openai/gpt-6-luna"
    assert settings.reasoning_effort == "low"
    assert "override_test_key" not in repr(settings)
    assert dict(os.environ) == before


def test_direct_scanner_key_takes_precedence_over_openai_key():
    settings = ScannerLLMSettings.load(
        environ={
            "OPENAI_API_KEY": "fallback_test_key",
            "MCP_SCANNER_LLM_API_KEY": "direct_test_key",
        }
    )
    assert settings.api_key == "direct_test_key"


def test_explicit_blank_scanner_key_is_not_replaced_with_fallback(tmp_path):
    file = tmp_path / "scanner.env"
    file.write_text(
        "OPENAI_API_KEY=fallback_test_key\nMCP_SCANNER_LLM_API_KEY=\n", encoding="utf-8"
    )
    with pytest.raises(ScannerLLMError, match="^SCAN_LLM_KEY_MISSING_OR_INVALID$"):
        ScannerLLMSettings.load(file, environ={})


def test_missing_key_does_not_silently_skip_llm():
    with pytest.raises(ScannerLLMError, match="^SCAN_LLM_KEY_MISSING_OR_INVALID$"):
        ScannerLLMSettings.load(environ={})


def test_arbitrary_environment_reference_is_rejected():
    with pytest.raises(SecretLoadError, match="^SCAN_LLM_KEY_REFERENCE_INVALID$"):
        load_scanner_environment(environ={"MCP_SCANNER_LLM_API_KEY": "${UNRELATED_SECRET}"})


def test_env_read_failure_hides_path(tmp_path):
    with pytest.raises(ScannerLLMError) as error:
        ScannerLLMSettings.load(tmp_path / "SENSITIVE_PATH_MARKER", environ={})
    assert str(error.value) == "SCAN_LLM_ENV_UNAVAILABLE"
    assert "SENSITIVE_PATH_MARKER" not in repr(error.value)


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("MCP_SCANNER_LLM_MODEL", "openai/another-model", "SCAN_LLM_PROFILE_UNSUPPORTED"),
        ("MCP_SCANNER_LLM_REASONING_EFFORT", "none", "SCAN_LLM_PROFILE_UNSUPPORTED"),
        (
            "MCP_SCANNER_LLM_BASE_URL",
            "https://untrusted.example.invalid",
            "SCAN_LLM_ENDPOINT_UNSUPPORTED",
        ),
        ("MCP_SCANNER_LLM_TIMEOUT", "nan", "SCAN_LLM_LIMIT_INVALID"),
        ("MCP_SCANNER_LLM_TIMEOUT", "inf", "SCAN_LLM_LIMIT_INVALID"),
        ("MCP_SCANNER_LLM_TIMEOUT", "0", "SCAN_LLM_LIMIT_INVALID"),
        ("MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS", "0", "SCAN_LLM_LIMIT_INVALID"),
        ("MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS", "-1", "SCAN_LLM_LIMIT_INVALID"),
        ("MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS", "invalid", "SCAN_LLM_LIMIT_INVALID"),
    ],
)
def test_invalid_configuration_fails_with_fixed_code(name, value, code):
    with pytest.raises(ScannerLLMError, match=f"^{code}$") as error:
        ScannerLLMSettings.load(environ={"OPENAI_API_KEY": "SENSITIVE_KEY_MARKER", name: value})
    assert "SENSITIVE_KEY_MARKER" not in repr(error.value)


def test_loader_returns_only_scanner_fields():
    values = load_scanner_environment(
        environ={"OPENAI_API_KEY": "test_key", "UNRELATED_SECRET": "SENSITIVE_UNRELATED_MARKER"}
    )
    assert "UNRELATED_SECRET" not in values
    assert "SENSITIVE_UNRELATED_MARKER" not in repr(values)


@pytest.mark.parametrize("previous", [None, "0", "1"])
def test_sdk_implicit_env_loading_is_disabled_and_flag_is_restored(monkeypatch, tmp_path, previous):
    from dotenv import load_dotenv

    if previous is None:
        monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    else:
        monkeypatch.setenv("PYTHON_DOTENV_DISABLED", previous)
    monkeypatch.delenv("SCANNER_IMPORT_SECRET_MARKER", raising=False)
    file = tmp_path / "scanner.env"
    file.write_text("SCANNER_IMPORT_SECRET_MARKER=synthetic_value\n", encoding="utf-8")
    with scanner_sdk_import_environment():
        assert not load_dotenv(file)
        assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert os.environ.get("PYTHON_DOTENV_DISABLED") == previous
    assert "SCANNER_IMPORT_SECRET_MARKER" not in os.environ
