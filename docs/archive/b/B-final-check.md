# B 제출 전 최종 점검

기준: main `f0c264392ef42eebf0521fa0a9ec9b1b9ad75e09` — [#109 병합](https://github.com/hsminu/MCP-Runtime-Guard/pull/109).

결론: B 구현·검증 범위의 제출 가능. 지정 검증은 통과했으며, 상태 문서 근거 표기와 B6.19·SCHEMA-01 연결·과거 보고서 호환은 후속으로 명시해야 한다.

## 1. 당시 검증

Windows, Python 3.12.15, 원본 저장소 `.venv`, `QT_QPA_PLATFORM=offscreen`, `PYTHONPATH=src`, 바이트코드 생성 금지. 테스트 임시 경로는 저장소 밖이다. `GUARD_TEST_CODEX_EXE`는 설정하지 않았다.

원본 저장소는 점검 시작 때 `wip/b-schema-2`였고 종료 확인 때 `main`이었다. 원본 reflog에 04:30:48의 브랜치 전환이 기록돼 있으나, 이 점검의 명령은 원본 브랜치를 전환하지 않았다. 검토 worktree는 `f0c2643` detached 상태다.

| 검사 | 결과·종료 코드 |
| --- | --- |
| Ruff / format | 각각 통과 / 0 |
| mypy | 140개 소스 파일 통과 / 0 |
| import-linter | `tools/ci/importlinter.ini` 지정, 7개 계약 유지 / 0. 최초 기본 설정 탐색은 설정 파일을 찾지 못해 1이었고 경로 지정 후 통과했다. |
| 계약 시험 | 45 passed / 0 |
| B 시험 90개 파일 | 2,767 passed, subtest 972 passed / 0 (85.60초) |
| 전체 pytest | 5,072 passed, 26 skipped, subtest 1,295 passed, warning 1 / 0 (652.38초). |

시험 실패·오류는 없어 소유자별 단독 재실행 대상이 없었다. 경고 1건은 `pydantic_settings`의 `lifespan` 미해결 전방 참조 경고다. 26건은 건너뛰었으며 실행하지 않은 검증을 통과로 세지 않는다.

## 2. 당시 B 상태 문서 정합성

- 완료 항목 89개. 그중 27개 줄에는 PR·커밋·기록 파일의 직접 근거 표기가 없다. 근거 부재를 확인한 것이 아니라 해당 줄에 표기가 없다는 뜻이다. ID는 아래 상세 목록에 있다.
- 완료 줄의 로컬 기록 링크 56개와 표기된 커밋 3개는 모두 존재한다. 직접 PR 링크 14개 및 GitHub 참조 36개도 조회 성공했다. 자동 검사는 참조의 존재를 확인하며, 각 참조가 주장 내용을 입증하는지까지는 증명하지 않는다. 검사한 로컬 앵커 1개도 일치했다.
- 완료 줄에서 직접 연결한 기록 42개에 나온 시험 참조 430건 중 현재 main과 불일치하는 것은 64건, 고유 58개 토큰이다. `test_guard`·`test_agt_real`은 시험명이 아닌 모듈 약칭이고, 나머지는 삭제·이름 변경·과거 시험 기록을 포함한다. 세부 이름과 원문 위치는 아래에 모두 적었다. 이 범위는 모든 역사 문서 전수가 아니다.
- B3.12의 A·C 연결 대기 문구는 현재보다 오래됐다. C2.14 파일 연산([#44](https://github.com/hsminu/MCP-Runtime-Guard/pull/44)), A 시작·저장([#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52))·Task 직렬화([#43](https://github.com/hsminu/MCP-Runtime-Guard/pull/43)), C 거절·누락 전송 계수([#56](https://github.com/hsminu/MCP-Runtime-Guard/pull/56)) 기록이 main에 있다. 항목 전체 인수 범위와 상태 갱신을 대조해야 한다.
- B1.9의 C #57 사례 확인 대기는 [#109 C 리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/109#issuecomment-6506140831)보다 오래됐다. B7.5의 `#B14 예정`과 상단 `이 브랜치에는 미병합`도 #109 병합 전 문구다. B7.7의 C 결함·B 재확인 대기는 [#105 B 재검증](https://github.com/hsminu/MCP-Runtime-Guard/pull/105#issuecomment-6505263530)과 병합 뒤 [#102 종료](https://github.com/hsminu/MCP-Runtime-Guard/issues/102)보다 오래됐다. 이 점검 당시 상태 문서를 바꾸지 않았다. 이후 59번에서 정리했다.

## 3. 당시 B 불변식 스모크

기존 B 시험만 사용했으며 합계 14 passed / 종료 코드 0이다. 단위 스모크는 C의 독립 wire·receiver 계수 인수를 대체하지 않는다.

| 불변식 | 시험 노드 | 결과 |
| --- | --- | --- |
| I3 재전송 금지 | `tests/unit/test_response_gate.py::test_send_transport_failure_is_unknown_and_not_retried` | 2 passed |
| I4 등급 단조 | `tests/unit/test_taint.py::test_observation_sequences_preserve_monotonicity_and_unclassified` | passed |
| I6 fail closed | `tests/unit/test_failclosed.py::test_failure_is_transformed_once_without_retry` | 6 passed |
| I7 출력 등급 불신 | `tests/unit/test_response_gate.py::test_output_label_cannot_override_configured_read_label` | passed |
| I8 승인 1회 소비 | `tests/unit/test_approvals.py::test_approved_ask_rechecks_consumes_and_dispatches_same_request_once` | 2 passed |
| I9 원문 비노출 | `tests/unit/test_catalog.py::test_argument_error_never_exposes_the_original_value` | passed |
| I10 지문 변경 무효화 | `tests/unit/test_approvals.py::test_security_or_registration_change_halts_and_invalidates[fingerprint]` | passed |

## 4. 당시 열린 B 항목

| 항목 | 담당과 남은 일 |
| --- | --- |
| B3.12 | A·C의 해당 작업 기록이 이미 있어 B가 상태 갱신과 실제 인수 범위를 대조해야 한다. [#43](https://github.com/hsminu/MCP-Runtime-Guard/pull/43)·[#44](https://github.com/hsminu/MCP-Runtime-Guard/pull/44)·[#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)·[#56](https://github.com/hsminu/MCP-Runtime-Guard/pull/56)·[#83](https://github.com/hsminu/MCP-Runtime-Guard/pull/83). |
| B6.8 | C 영속 순번 배정·허용 실패 감사 연산 자체의 독립 인수. [#103](https://github.com/hsminu/MCP-Runtime-Guard/pull/103)·[#104](https://github.com/hsminu/MCP-Runtime-Guard/pull/104)는 재시작·변조와 60회 강제 종료 복구를 확인했다. |
| B6.19 | A·B·C의 정의 전용 generation과 예약/소비·worker 전송 경쟁 설계 및 C 독립 인수. 현행 동시 I10 해결은 주장하지 않는다. [#106 C 답](https://github.com/hsminu/MCP-Runtime-Guard/issues/106#issuecomment-6505180007). |
| B7.2 | C의 `core/policy.py`와 `services/response_gate.py` 직접 리뷰 근거를 확인한다. [B 현황](https://github.com/hsminu/MCP-Runtime-Guard/blob/f0c2643/docs/b-status.md). |
| B7.5 | C worker launcher·자원 측정과 A 조립·종료 연결. 현재 제품 조립이 runner를 주입하지 않아 in-process 경로가 남는다. [#109 A](https://github.com/hsminu/MCP-Runtime-Guard/pull/109#issuecomment-6505998748)·[C 리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/109#issuecomment-6506140831). |
| B7.8 | C 독립 통합 인수와 기준 커밋 정리. C T/S 범위는 완료 33·부분 3·미실행 5로 기록돼 있다. [C6.25-2](https://github.com/hsminu/MCP-Runtime-Guard/blob/f0c2643/docs/archive/c/C6.25-2-verification-summary.md). |

## 59번 B3.12 대조 (main `f0c2643` 기준)

| 요소 | 근거와 범위 |
| --- | --- |
| A81 허용 체크 | [A 활성화 잠금 기록](../a/A105-activation-lock.md), [#83](https://github.com/hsminu/MCP-Runtime-Guard/pull/83): READ·SEND 기본 체크와 해제 거부, 실제 SQLite·Registry와 합성 peer 수신 0 확인. |
| CFG-01 시작·저장·적용 | [A 설정 연결](../a/A129-settings-file-registry-connection.md), [#44](https://github.com/hsminu/MCP-Runtime-Guard/pull/44)·[#52](https://github.com/hsminu/MCP-Runtime-Guard/pull/52)·[#83](https://github.com/hsminu/MCP-Runtime-Guard/pull/83): C 파일 읽기·원자 교체, A 시작·저장 및 B 적용 연결. |
| Task 생성 직렬화 | [A Task 생성 잠금](../a/A105-task-creation-policy-lock.md), [#43](https://github.com/hsminu/MCP-Runtime-Guard/pull/43): 설정 저장·적용과 같은 잠금에서 최신 정책으로 생성. |
| C 거부·누락 전송 0 | [C 접근 규칙 계수](../c/C6.9-access-rule-counts.md), [#56](https://github.com/hsminu/MCP-Runtime-Guard/pull/56): stdio·HTTP의 승인 소비·dispatch·wire·수신 효과 0. HTTP는 같은 프로세스이며 실제 Codex 세션 인수는 별도다. |

기존 네 요소는 완료다. A184 사용자 결정은 설정 파일이 없으면 B가 검증한 내장 빈 Profile(선택 서버·도구·목적지 없음, 도구 0)로 Task를 허용한다. [#110](https://github.com/hsminu/MCP-Runtime-Guard/pull/110)은 59번 점검 당시 병합 대기이므로 main의 기존 파일 누락 동작과 구분한다. `docs/contracts.md` CFG-01의 기존 문구는 이 결정에 맞게 바꾸도록 제안하며 직접 수정하지 않았다.

## 과거 시험명과 현재 대응

아래는 58번이 완료 항목의 연결 기록 42개에서 찾은 불일치 중 실제 대응을 확인한 것만 적었다. 과거 기록 자체는 변경하지 않는다. 삭제된 AGT 대상 시험은 제거 결정에 따른 역사이며, 각각의 일대일 대체 시험을 주장하지 않는다. `test_guard`·`test_agt_real`은 문맥상 모듈 약칭이다. B7.10의 과거 수집 실패에 나온 파일은 그 시점의 기록이다.

| 과거 이름 | 현재 시험과 변경 |
| --- | --- |
| `test_settings_that_differ_from_the_registration_stop_the_assembly` | `tests/unit/test_product_settings.py::test_settings_that_differ_from_the_registration_hide_only_that_server`: 이름 변경을 원 기록이 명시한다. 이후 B14 시작 제외에 맞춰 기대가 바뀌었다. |
| `test_executed_released_serializes_only_released_payload_and_preserves_success_meaning` | `tests/unit/test_final_model_messages.py::test_executed_released_returns_empty_marker_and_preserves_success_meaning`: 봉투 대신 빈 표시와 성공 의미를 확인. |
| `test_unvalidated_released_body_never_enters_errors` | `tests/unit/test_final_model_messages.py::test_executed_released_does_not_read_or_validate_body`: 본문 비조회 확인. |
| `test_serialization_failure_has_no_raw_exception_context` | `tests/unit/test_final_model_messages.py::test_executed_released_does_not_serialize_non_utf8_body`: 본문 비직렬화 확인. 기존 예외 문맥 검사의 동등성 전체를 주장하지 않는다. |

과거 AGT·CLI 관련 시험 등 위에 없는 이름은 일대일 대응을 확인하지 못했다. 58번의 불일치 58개는 전체 archive 전수 결과가 아니다.

## 당시 제출용 B 요약

- 기준: main `f0c2643`, B14 [#109](https://github.com/hsminu/MCP-Runtime-Guard/pull/109) 병합.
- B는 불변 도메인·포트와 접근·정책·등급 누적을 구현했다.
- Catalog는 신뢰 등록·Profile·출처·목적지와 인자 검증을 결합한다.
- Guard·승인은 예약 전 거부와 예약 후 격리를 구분하고 승인 재평가·1회 소비를 지킨다.
- SQLite는 단일 writer, 원자적 상태·감사, 영속 순번과 명시 시작·복구를 구현한다.
- SCHEMA-01 B는 준비 캐시와 비동기 검증 경계를 구현했다. A 조립과 C worker 연결은 남았다.
- B 스모크는 재전송 금지, 등급 단조, fail closed, 출력 등급 불신, 승인 1회, 원문 비노출, 지문 무효화를 확인했다.
- C3의 강제 종료 60회와 재시작·변조는 [#103](https://github.com/hsminu/MCP-Runtime-Guard/pull/103)·[#104](https://github.com/hsminu/MCP-Runtime-Guard/pull/104) 근거다.
- C5.4는 승인 소비·dispatch·wire·receiver 계수를 연결했고, C5.6은 응답 유실 재전송을 확인했다.
- B6.19의 정의 변경·예약·worker 전송 동시 경쟁 해결과 독립 인수는 후속이다.
- 구형 `Finding.code`가 새 허용 집합 밖인 저장 보고서는 등록 조회가 실패할 수 있다. 실제 해당 행 존재는 미확인이다.
- B6.8의 영속 순번·실패 감사 독립 인수와 B7.8 통합 인수도 남아 있다.
- 상태 문서의 오래된 문구와 근거는 이후 59번에서 정리했다. 역사적 시험명은 아래 대조표를 본다.
- 최종 검증: Ruff·format·mypy·import-linter, 계약 45개, B 2,767개, 전체 5,072개 통과(26 skipped, 종료 0).
