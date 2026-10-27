# A68 · 승인 결과 문구를 `final_model_message`로 교체

2026-10-19. #32(B 3차) B7.11에서 B가 `final_model_message(PendingFinal)`를 추가했다. 이걸 A 쪽에 연결했다. #32가 아직 main에 병합되지 않아서, `origin/feat/b-security-core`(`f617624`)를 A 브랜치에 로컬로 병합한 위에서 작업했다.

## 한 일

- `services/guard_relay.py`: A가 임시로 두었던 비실행 원인 문구표(`_FINAL_MESSAGES`)를 지웠다. 이제 거절·만료·취소·무효·실패·종료 문구는 B의 `final_model_message`에서 받는다.
- executed는 지금처럼 `_answer`로 처리한다. 바로 나온 Released·Denied와 같은 변환이다(#32 A 리뷰 1번, B 동의).
- failed·shutdown은 B 표의 STOPPED 문구를 그대로 쓴다. A는 cause를 보고 세션 정지 코드(`CODEX_TOOL_STOPPED`)를 계속 붙인다.
- `final_model_message`가 `ModelMessageError`를 내면, 예를 들어 비실행 원인인데 결과가 Denied가 아니면, 원문을 보내지 않고 정지 문구로 답한 뒤 Task를 멈춘다.

## 검증

- `test_codex_session.py`
  - 기존 원인별 문구 테스트 5개와 정지 테스트 5개가 그대로 통과한다.
  - 검증을 우회한 `PendingFinal`(cause=rejected, outcome=Released) 경우를 정지 테스트에 추가했다. 본문이 모델로 나가지 않는 것까지 확인한다.
- ruff·format·mypy 통과.
- 전체 테스트(`tests` 전체, offscreen): 4,224개 통과, 1 skip, 1 실패.
  - 실패한 1건은 `test_access_rules.py::test_decision_and_normalization_are_deterministic`이다. 이번에 바꾼 파일과 관계없고, A97 기록에서도 같은 간헐 실패가 있었다.
  - 단독으로 세 번 다시 돌리면 모두 통과했다. 원인은 확인하지 못했다.

## 남은 것

- 로컬 A 브랜치에만 커밋했다. #32가 main에 병합되면 main을 합친 뒤 #29에 올린다.

관련: I5, I9, NOTIFY-01.
