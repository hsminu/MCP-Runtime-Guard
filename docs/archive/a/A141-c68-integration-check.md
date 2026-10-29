# A141 · C의 HTTP 종료 기대값 수정 통합과 전체 재검사

2026-10-29. [A 후속 PR #70](https://github.com/hsminu/MCP-Runtime-Guard/pull/70)의 통합 검증이다.

## 한 일

- C가 [PR #68](https://github.com/hsminu/MCP-Runtime-Guard/pull/68)의 `184ce8582175d0b6d87c8ce2b55105a6371dc883`에서 A132에 맞게 HTTP `CLOSE_ERRORS` 기대값 4건을 수정했다. main 병합 커밋은 `30d89d726e34dea928ff06acbc0334146fe959cb`다.
- A는 C 소유 테스트를 직접 수정하지 않고 main을 A 브랜치에 `0f21defdfed9e17657533a73a9593ff9c1460354`로 병합했다. 이 병합의 파일 차이는 `tests/unit/test_write_loss_counts.py`뿐이다.
- A115·A136 `4c0aa43`, A140 `94c0668`, 기존 main 병합 `232cfe8`·`118e43e`·`d4e028e`·`8e33e3b` 이력을 보존했다. 원격 대상은 `feat/a-application`이며 강제 푸시하지 않았다.
- [A140 당시 결과](A140-list-change-before-seal.md)의 4 실패는 당시 사실로 보존한다. A115·A136 및 기존 archive를 수정하지 않았다.

## 실제 검증

대상은 `0f21def`의 제품·테스트 소스다. Windows / Python 3.12.15 / mcp 1.29.0 / pytest 9.1.1 / Ruff 0.17.0 / mypy 2.4.0, 로컬 `.venv`, `QT_QPA_PLATFORM=offscreen`으로 실행했다.

- 전체 단위: **5,468 통과, 실패 0, 5 skip, 20 subtest 통과**, 270.49초.
- contract: **45 통과**, 7.09초.
- Ruff check·format(267개 파일), mypy(112개 소스) 모두 통과했다.
- 명령: `.venv/Scripts/python.exe -m pytest -q -rs tests/unit`, `-m pytest -q tests/contract`, `-m ruff check .`, `-m ruff format --check .`, `-m mypy`.
- skip 5개는 심볼릭 링크 권한 부족 4개(`test_codex_home`, `test_paths`, `test_safe_path`, `test_settings_io`)와 `GUARD_TEST_CODEX_EXE` 미설정으로 실제 L2를 실행하지 않은 1개(`test_codex_l2`)다.
- 경고 1개는 `pydantic_settings`의 `lifespan` 미해결 forward reference다.
- A140의 수신 순서·서버 격리(I2·I7·I10, T10)와 A136의 종료 확인·미확인 잠금 유지(REC-01·REC-05)는 기존 단위 테스트와 함께 실행됐다. 관련 95개 및 수신 표시를 비활성화한 회귀 대조 3개 실패의 상세 근거는 A140 기록에 있다.

## 남은 한계

- 이 결과는 로컬 전체 단위·contract·정적 검사다. 원격 CI는 해당 PR의 head별 결과를 따로 확인한다. 실제 Codex 모델·보안 스캔·C 독립 인수 완료를 뜻하지 않는다.
- A115 C 리뷰, A140 B·C 리뷰와 C5.9 실제 서버 알림 시점 관찰은 남아 있다.
- A134 실제 writer 경합 재현은 [B 실행 조건 요청](https://github.com/hsminu/MCP-Runtime-Guard/pull/58#issuecomment-6473123051) 대기다. A136의 실제 5초 이상 멈춘 writer는 검증하지 않았다. A137 bridge 시험 빈틈·A138 B6.8 제품 연결도 별도 후속이다.
- 공통 계약·설계·검사 관리대장은 별도 main 대상 문서 PR로 제안하며 이 A 브랜치에서 수정하지 않는다.
