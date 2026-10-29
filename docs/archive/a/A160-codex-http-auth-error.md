# A160 · 실제 HTTP 401의 인증 실패 안내

2026-10-29 · A · 관련 I6·I8·I9, 개발문서 10.4·12절, A149, T19·S04의 오류 비노출 보조 범위.

## 발견과 수정

- 고정 Codex 0.162.1에 비밀이 아닌 잘못된 테스트 키로 로그인하고 실제 모델 첫 요청을 보냈다. `turn/completed`의 `codexErrorInfo`는 `{"httpConnectionFailed":{"httpStatusCode":401}}`이었다. 메시지 원문·키·전체 RPC는 수집하지 않았다.
- 기존 `TurnAnswers.answer()`는 문자열 `unauthorized`만 처리했다. dict를 사용량 제한 문자열 집합에서 조회하면서 TypeError가 발생해 일반 `CODEX_SESSION_FAILED`로 바뀌었고, 사용자는 올바른 키를 입력하라는 안내를 받지 못했다.
- 알려진 `httpConnectionFailed` 단일 태그와 정수 HTTP 401을 `CodexAuthenticationError`로 바꿨다. 사용량 제한 집합 조회는 str에만 수행한다. 다른 태그·형식 오류는 기존 고정 실패로 남는다. 오류 원문을 분석하거나 노출하지 않는다.
- 관리 대상 도구·승인·전송 정책, 계약, 재시도 동작은 바꾸지 않았다. C 소유 L2 경로 비교는 사용자 지시대로 수정하지 않았다.

## 실제 검증

- `tests/unit/test_codex_session.py`, `test_codex_task_runner.py`, `test_ui_model_settings.py`, `test_a_architecture.py`: **56 passed, 10 subtests passed, 7.92초**.
- 401 객체의 인증 실패, 500·문자열 상태 코드·모르는 태그·빈 세부값·list 형태의 고정 실패를 기존 세션 시험에 더했다. 모든 경우 `PRIVATE` 오류 원문 비노출을 확인했다.
- 변경 파일 Ruff check·format check 통과, 전체 mypy **118개 소스** 통과.
- 수정 후 새 실제 데스크톱 프로세스에서 잘못된 키의 첫 요청을 다시 보냈다. 같은 구조화된 HTTP 401을 관찰했고, 화면에 `API 키가 틀렸거나 Codex 인증이 만료되거나 갱신되지 않아…`와 세션 종료·로그아웃·올바른 키 입력·새 세션 안내가 표시됐다. Task는 HALTED, 전송 비활성, Codex 종료 확인 True/returncode 0이었다.

## 한계

- Codex 데스크톱의 MSIX 경로 가상화 때문에 기본 앱 경로에서 기존 C L2가 실패한다. 이 실제 요청 검증은 같은 실제 폴더를 `CodexProcessPreparation(root=app_root().resolve())`로 지정했다. L2 평가나 모델 응답을 대체하지 않았다. 기본 시작 실패를 이 수정으로 해결했다고 주장하지 않는다.
- C/B 독립 리뷰와 T43 전체 인수는 별도다. 외부 인증 서버 오류의 모든 변형을 일반화하지 않는다.

PR: 미생성.
