"""Scanner 환경 파일·키 로딩 경계(I9). 키를 프로세스 환경이나 로그에 복사하지 않는다."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from dotenv import dotenv_values

_SCANNER_KEYS = (
    "OPENAI_API_KEY",
    "MCP_SCANNER_LLM_API_KEY",
    "MCP_SCANNER_LLM_MODEL",
    "MCP_SCANNER_LLM_REASONING_EFFORT",
    "MCP_SCANNER_LLM_BASE_URL",
    "MCP_SCANNER_LLM_TIMEOUT",
    "MCP_SCANNER_DEFAULT_LLM_MAX_TOKENS",
)


class SecretLoadError(ValueError):
    """경로·파일 내용·키를 노출하지 않는 설정 로딩 오류."""


@contextmanager
def scanner_sdk_import_environment() -> Iterator[None]:
    """SDK 첫 import의 암묵적 load_dotenv를 제한하고 기존 플래그를 복원한다.

    Cisco 4.8.6의 패키지 import는 HTTP 서버 모듈도 불러오며 그 모듈이
    load_dotenv를 실행한다. 이 동기 구간에는 await를 넣지 않는다.
    """
    previous = os.environ.get("PYTHON_DOTENV_DISABLED")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("PYTHON_DOTENV_DISABLED", None)
        else:
            os.environ["PYTHON_DOTENV_DISABLED"] = previous


def load_scanner_environment(
    env_file: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """명시한 파일 위에 프로세스 설정을 적용한다. 자동 파일 탐색은 하지 않는다.

    키 별칭은 병합한 OPENAI_API_KEY로 확장한다. 임의 환경변수 참조나 외부
    명령 확장은 허용하지 않는다. 반환값은 비밀값이므로 로그에 공개하지 않는다.
    """
    values: dict[str, str] = {}
    if env_file is not None:
        try:
            if not env_file.is_file():
                raise SecretLoadError("SCAN_LLM_ENV_UNAVAILABLE")
            file_values = dotenv_values(env_file, interpolate=False, encoding="utf-8")
        except (OSError, UnicodeError):
            raise SecretLoadError("SCAN_LLM_ENV_UNAVAILABLE") from None
        values.update(
            {
                key: value
                for key, value in file_values.items()
                if key in _SCANNER_KEYS and value is not None
            }
        )
    source = os.environ if environ is None else environ
    values.update({key: source[key] for key in _SCANNER_KEYS if key in source})
    alias = values.get("MCP_SCANNER_LLM_API_KEY", "${OPENAI_API_KEY}")
    if alias == "${OPENAI_API_KEY}":
        alias = values.get("OPENAI_API_KEY", "")
    if "${" in alias:
        raise SecretLoadError("SCAN_LLM_KEY_REFERENCE_INVALID")
    values["MCP_SCANNER_LLM_API_KEY"] = alias
    return values
