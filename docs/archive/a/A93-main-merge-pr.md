# A93 · A 브랜치 main 병합 PR

2026-10-18. A 브랜치를 PR #3 이후 다시 main 대상으로 올렸다. B1.7·B2.11(AGT·ModelClient 제거)과 C2.10·C5.8이 이 병합을 기다린다.

## 한 일

- [PR #29](https://github.com/hsminu/MCP-Runtime-Guard/pull/29)(`feat/a-application` → `main`)를 올렸다. 커밋 41개, 파일 100개.
- domain·ports와 B·C 소유 파일은 바꾸지 않았다. 공통 설정은 `pyproject.toml` 하나만 바꿨다(A43). 의존성은 그대로다(ENV-01).
- PR 설명에 담당 범위, 관련 I/T ID, 검증 결과, 실행하지 않은 것, 남은 막힘을 적었다.
- 2026-10-19 소유권 표에 따라 리뷰를 요청했다. B(Neo6424)는 `runner.py`·`mcp_transport.py`·`AccessChecker` 연결을, C(hsminu)는 `runner.py`·`bridge.py`·`ui/*`를 본다.

## 검증

- PR CI 통과(GitHub Actions, 6분 8초). main과 충돌 없이 병합 가능하다.
- 올리기 전 전체 단위 테스트(`QT_QPA_PLATFORM=offscreen`) 3,492 통과·1 skip, ruff check·format·mypy(81파일) 통과.
- PR #28(B 2차)과 충돌 없이 합쳐지는 것을 확인했다.

## 남은 것

- 리뷰와 병합은 B·C 리뷰와 사용자 병합을 기다린다.
- 실제 codex.exe·C launcher·L2·Registry·keyring은 실행하지 않았다(C 대기).
- PR #28이 병합되면 A 임시 연결 3곳(`ApprovalOutcomes`, `HumanControl.cancel` 정지 경로, 정책 저장)을 후속 PR로 교체한다.

관련: I1·I6–I11, T10·T14·T18·T21·T22(L3 부분)·T39–T45(A 부분).
