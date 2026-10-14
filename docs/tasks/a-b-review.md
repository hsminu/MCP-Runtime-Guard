# A23 · B Guard/Catalog의 A 연결 관점 리뷰

작성 기준: 2026-10-12. 검토 대상은 B 커밋 `0c02b616f433d0a5c9b7a2815cf3f2bb3d3954cc`의 `src/guard_app/services/guard.py`, `catalog.py`다. 현재 체크아웃은 `feat/a-application`이며 B 브랜치로 전환하지 않았다. 모든 B 소스는 `git show 0c02b61:<경로>`로 읽었다. A03의 [B 포트 검토](a-port-review.md)와 A04의 [C 플랫폼 요구](a-platform-review.md)는 선행 자료이며, 이 문서는 공동 계약 합의 기록이 아니다.

## 범위와 결론

`guard.py`, `catalog.py` 전부와 직접 맞닿는 `approvals.py`, `response_gate.py`, `catalog_schema.py`, `catalog_sources.py`, `domain/models.py`, `domain/contracts.py`, `ports.py`, `core/policy.py`, `security/failclosed.py`, 해당 단위 테스트를 읽었다. 설계 0.4·0.5·4·6–10·14.3절 및 A23 프롬프트와 대조했다. **이 버전의 Guard/Catalog에서 재현 가능한 소스 근거가 충분한 finding은 0건이다.** 이것은 실제 A·C 구현 연결, SQLite 원자성, MCP 전송, 수신 계수, 공격·결함 주입 검증의 통과 판정이 아니다.

| 검토 경로 | B 코드에서 확인한 동작 | A 연결 시 의미 / 설계 대응 |
| --- | --- | --- |
| 단일 실행 입구와 예약 | `GuardService.submit`이 Task lock 안에서 `_decide`를 호출하고, `Catalog.resolve`·정책/AGT 판정·결정 기록을 거쳐 ALLOW만 예약한다. ASK는 `Pending` 반환 전에 승인 서비스가 WAITING 상태를 만든다 (`guard.py:160–230`). | Runner는 신뢰 Task ID와 `ProposedCall`만 `submit`에 전달한다. Transport를 별도 실행 API로 연결할 이유가 없다. I1·I2·I3, T08–T12. |
| Catalog의 신뢰 해석 | 첫 `__`로 서버 ID와 도구 이름을 나누고 ACTIVE 등록 및 현재 지문을 확인한다. 등록 스키마로 인자를 검증하고, SEND 목적지는 등록 경계 ID와 정확히 대조하며, 자원형 READ는 C resolver가 만든 canonical ID만 쓴다 (`catalog.py:76–102, 168–215`). 모델용 목록도 ACTIVE/지문 일치 도구만 내보낸다 (`catalog.py:125–151`). | 모델 이름·인자·서버 주장 등급이나 `stdio`/HTTP 방식에서 신뢰를 만들지 않는다. C Registry·안전 경로 연결이 필요하다. I6·I7·I10, T04·T26·T27·T32. |
| 등록 변화와 전송 직전 재확인 | AGT를 기다린 뒤 현재 status/지문으로 다시 판정하고 (`guard.py:233–285`), 예약 후 `_dispatch_and_commit`에서도 Task 보안 맥락과 Catalog 재해석 결과가 원래 요청과 같은지 확인한 뒤 `Transport.call`을 부른다 (`guard.py:304–364`). | A AccessChecker는 판정만 반환하고, A worker는 Guard에서 받은 요청만 실행해야 한다. C의 등록 변경 전파·승인 무효화는 별도 인수 조건이다. I2·I10, T10·T29. |
| 승인 lock과 소비 | 승인 대기 중 `submit`의 Task lock은 풀린다. 사람 승인 시 같은 lock을 다시 잡아 재평가·사본 해시 대조·`consume_and_reserve` 성공 뒤 Guard 예약 실행을 끝까지 유지한다 (`approvals.py:211–247, 249–385`; `guard.py:287–302`). | A의 인증된 제어 큐에만 `HumanControl`을 보관하고 모델/실행 큐에 노출하지 않는다. 실제 1회 소비·dispatch는 C Store·fixture 계수로 검증해야 한다. I3·I4·I5, T09–T12·T37. |
| 결과 공개·오류 | READ는 결과 형식/크기와 출처를 검사하고 `commit_observation` 반환을 검증한 뒤 `Released`를 만든다. SEND 성공에는 READ 등급 병합을 하지 않는다 (`response_gate.py:67–152`). 예약 후 예외는 READ `Withheld`/HALTED, SEND `OutcomeUnknown`/UNKNOWN_OUTCOME으로 격리한다 (`guard.py:287–302, 401–435`). | Runner·UI에는 `Released.result`만 본문으로 전달한다. A transport의 결과 변환·stderr/progress 격리와 C의 실제 커밋 내구성은 별도 검증이다. I4–I6·I8·I9, T33·T35·T36·T38. |

## Finding과 미확정 연동 결정

확인된 finding은 없다. 다음은 현재 코드 결함으로 단정하지 않은 연동 결정이며, A03/A04의 미합의 계약을 대체하거나 반복 확정하지 않는다.

1. `ApprovalService.open`은 `set_task_status(WAITING_APPROVAL)` 뒤 `create_approval`을 별도로 호출한다 (`approvals.py:156–207`). B는 중간 실패를 `Pending`으로 숨기지 않고 global halt로 처리하며, 두 작업의 원자 묶음은 B/C 합의 사항이라고 명시했다. C가 실제 Store에서 어느 트랜잭션 경계로 보장할지 결정하고 계약 테스트를 제공해야 한다. I3, T11·T12.
2. 소비 뒤 WAITING→RUNNING의 `security_version`은 현재 Guard가 기존 값 또는 `+1`을 허용한다 (`guard.py:313–327`). B/C가 실제 Store 전이 의미를 정해야 A가 상태 리비전·이벤트를 연결할 수 있다. I3·I4, T12·T37.
3. `Registry` 상태·지문 조회와 변경 시 PENDING 복귀/기존 승인 무효화는 C 구현 계약이다 (`catalog.py:168–187`; `ports.py`의 `Registry` 주석). B의 호출 시점 재검사는 확인했지만, 실제 변경 전파의 원자성은 이 커밋만으로 증명되지 않는다. I10, T10·T17.
4. 모델용 `ToolView.description`, 승인 원문 사본의 인증된 조회 수명, UI 이벤트/리비전과 C 계수·fixture 요구는 [A03](a-port-review.md)·[A04](a-platform-review.md)에 이미 정리되어 있다. 이 리뷰에서 코드 finding으로 다시 분류하지 않았다.

## 실제 검증과 한계

- B 커밋의 `src/guard_app`와 `tests/unit`을 각 파일의 `git show` 결과로 작업공간 안의 일회용 격리 디렉터리에 복사해 실행했다. Python 3.12.15, pytest 9.1.1, pydantic 2.14.0, jsonschema 4.25.1의 현재 `.venv`를 사용했다. `PYTHONPATH`는 그 사본의 `src`였다. `python -m pytest -q -p no:cacheprovider tests/unit/test_catalog.py`: **71 passed in 0.75s**. 일회용 디렉터리는 정리했다.
- Guard/승인/응답 게이트 묶음 실행은 Catalog 테스트의 71개 완료 표시 뒤 진전 없이 중단했다. 별도 Guard/Catalog 6건 실행도 5개 완료 표시 뒤 45초 제한에 걸렸고, `test_registry_change_after_reserve_is_contained_without_transport` 단독 실행은 15초 제한에 걸렸다. 단독 실행에 `faulthandler_timeout=5`를 설정하니 스택은 테스트의 `asyncio.run()`에서 Windows 이벤트 루프를 만드는 `socket._fallback_socketpair`/`accept`에 있었고 Guard 함수에 진입하지 않았다. 따라서 이 시간초과를 Guard 코드 finding이나 해당 테스트 통과로 취급하지 않는다. Guard suite와 승인/응답 게이트 suite의 **완료 결과는 미확인**이다.
- B 인수 문서의 `840 passed`는 B 측 실행 기록이지 이번 A23에서 재실행한 결과가 아니다. 실제 MCP stdio/HTTP, AGT, C SQLite·Registry·fixture, `approval_consumed / host_dispatch / wire_tools_call / receiver_effect`, T08–T12·T26·T27·T29–T38 통합/공격/결함 주입은 이 리뷰에서 **미실행**이다.

다음 인수: B/C가 위 Store·Registry 결정을 공동 계약으로 확정하고 실제 구현을 제공하면, A는 Guard 입구·Catalog view·사람 제어 큐를 연결한다. C의 동일 계약 테스트와 요청별 계수·결함 주입 결과를 받아 이 리뷰의 미검증 항목을 갱신해야 한다. B 코드 수정, 담당자 메시지, PR 게시, `docs/contracts.md` 합의 표 갱신은 하지 않았다.

## 후속 B 변경 확인

사용자의 B 브랜치 활용 결정 뒤 원격 HEAD가 `f3c2d2bc225e0406c50731c9b63779a3d890cd70`으로 전진했다. `git diff 0c02b61 f3c2d2b`로 변경 범위를 확인했다. B의 공개 `domain/*`·`ports.py`·Catalog·ResponseGate는 바뀌지 않았고, `guard.py`와 `approvals.py`에 승인 사본의 Task ID/epoch와 실제 Store 행의 연결을 검사하는 수정이 들어왔다. 현재 A 작업 트리의 두 파일은 이 최신 B 커밋에서 가져왔다. 위 표의 A23 정적 리뷰·71개 Catalog 테스트 결과는 여전히 **이전 `0c02b61` 기준**이며, 새 승인 테스트와 전체 Guard suite를 A에서 통과로 주장하지 않는다. A adapter/Runner가 사용하는 공개 포트 변경은 확인되지 않았다.

## 후속 보안 검토 발견

2026-10-12 A 브랜치 `75a52c6`에서 별도 Codex Security 소스 스캔이 B 소유 `catalog_schema.py`의 정규식 가용성 문제(CWE-1333, medium)를 발견했다. 위 A23의 finding 0건은 당시 Guard/Catalog 연결 관점 리뷰 결과이고, 이 후속 발견을 부정하는 보안 감사 판정이 아니다. `_check_pattern`은 `pattern`·`patternProperties`를 `re.compile`로 문법만 검사한다. `ArgumentSchema.validate`와 A 모델 변환은 등록 패턴을 긴 인자에 대해 동기 매칭할 수 있다. 실제 ACTIVE 등록·모델 입력 경로는 C 구성 전이므로 공격 도달성은 아직 조건부다.

B3 문서는 `patternProperties` 지원을 명시한다. 등록 단계에서 두 정규식 키워드를 모두 거부하면 가용성 위험은 줄지만 B3 기능 계약이 바뀐다. 단순 문자열 길이 상한이나 위험 패턴 휴리스틱은 보장을 주지 못한다. B가 지원 범위 변경 또는 검증기 전체의 제한된 실행 방식을 결정하고, C가 ACTIVE 등록·긴 인자·제어 응답성 테스트를 제공해야 한다. A는 모델 응답/인자 수신 예산을 별도로 강화하되 이 B 문제를 해결했다고 표시하지 않는다. 상세한 경계·ARTEX 실행 조건은 [A 보안 검토](a-security-review.md)에 있다.
