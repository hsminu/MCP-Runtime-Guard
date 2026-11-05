# A221 · Codex 보안 검토의 A 단독 수정 5건(D-01·02·05·06·07)

2026-11-05 · `feat/a-application` · PR #118. 출처: Codex 보안 검토(`gpt-daybreak-blue-latest`, reasoning effort xhigh, 읽기 전용, `a_modules()` 90개 파일 정적 검토), 사용자 지시("B,C 소견 없어도 되는 부분 다 고쳐서 PR").
관련: A76·A77(`tools/list` 수신 상한), A88·A90(Codex 세션·답변 스트리밍), A89(기기 코드 로그인), A191(채팅 답변 블록), A220(같은 PR).

## 검토 결과와 범위

검토는 높음 0건, 보통 6건, 낮음 1건이었다. 7건 모두 A가 코드에서 해당 구조가 실제로 있는지 확인했다. 이 기록은 B·C 합의 없이 A가 고칠 수 있는 5건이다.

| ID | 내용 | 이번 처리 |
| --- | --- | --- |
| D-01 | `tools/call`·initialize 응답은 SDK 파싱 전 바이트 상한이 없음 | 수정 |
| D-02 | Codex 완료 알림을 turn 대조·누적 상한 없이 보관하고 지우지 않음 | 수정 |
| D-03 | `turn/completed`가 안 오면 turn이 기한 없이 기다림 | 제외: 시간 값 결정 필요 |
| D-04 | 검토 안 된 schema 주석이 그대로 Codex `dynamicTools`로 감 | 제외: A·B 합의 필요(DESC-01) |
| D-05 | `item/tool/call`이 thread만 대조하고 turn은 대조하지 않음 | 수정 |
| D-06 | 짧은 블록을 반복하면 답변 위젯이 수만 개 생길 수 있음 | 수정 |
| D-07 | 기기 로그인 URL·코드를 빈 값인지만 보고 그대로 표시 | 수정 |

## 한 일

### D-01 · MCP 메시지마다 수신 상한

- `IngressGate`에 항상 적용되는 메시지 상한 `MAX_MESSAGE_BYTES = 8 MiB`를 두었다(`check_message`).
  - `tools/list` 수집 중이 아니어도 적용된다. 대상은 stdio 프레임, HTTP JSON 본문, SSE `data`, SSE의 다른 필드, 그대로 통과시키던 본문(JSON·SSE가 아닌 media, 지원하지 않는 content-encoding)이다.
  - 모두 압축 해제 뒤, UTF-8·JSON 파싱 전에 센다. 넘으면 `MCP_MESSAGE_TOO_LARGE`로 그 연결의 게이트가 실패한다(기존처럼 고정 코드, 재시도 없음).
  - 값 8 MiB: worker 결과 복사 상한의 설정 최대치(`MAX_CONFIGURED_RESULT_COPY_BYTES`), Codex 프레임 상한과 같다. 기본 복사 상한 512 KiB의 JSON 이스케이프 여유도 충분하다.
- 호출 중에 게이트가 실패하면 기존 `_watch_ingress` 경로로 worker가 멈춘다. 호출은 dispatch 뒤 `DispatchError("McpError")`가 되어 Guard가 READ는 미공개 정지, SEND는 결과 불명으로 다룬다(T36과 같은 경로).
- `result_budget.py`의 "C 쪽 상한이 따로 필요하다" 설명을 이 상한으로 고쳤다. SDK가 직접 띄우는 stdio(시연 전용)에는 게이트가 없다는 점도 적었다.

### D-02 · Codex 답변 보관 상한과 정리

- `TurnAnswers`가 아직 답하지 않은 turn에 대해 보관하는 양을 센다.
  - 글자 수 합 `MAX_RETAINED_CHARS = 4 × 65,536`, turn ID 수 `MAX_TRACKED_TURNS = 8`
  - delta, 완료 메시지, `turn/completed`가 모두 이 상한으로 들어간다. 넘으면 세션을 `CODEX_ANSWER_TOO_LARGE`로 정지한다.
- `turn/completed`는 `answer()`가 읽는 상태와 `codexErrorInfo`만 남긴다.
- `answer()`가 답을 돌려준 뒤에는 그 turn의 완료 기록만 남기고 나머지 turn 상태를 모두 비운다. 그래서 긴 세션도 쌓이지 않는다. `interrupt()`가 보는 `completed(turn_id)`는 그대로 유지된다.

### D-05 · 도구 호출과 turn 결속

- `item/tool/call`은 `send()`가 기다리는 turn의 호출만 Guard로 보낸다.
  - 거절 대상: 진행 중인 send가 없을 때, `turn/start`가 돌려준 turn과 다를 때, 이미 끝난 turn
  - 거절하면 고정 정지 문구로 답하고 `CODEX_TOOL_CALL_INVALID`로 정지한다(기존 지연 정지).
- `turn/start` 응답보다 먼저 읽힌 호출은 자기 task에서 응답을 기다렸다가 대조한다.
  - 응답이 오거나 send가 끝나면(성공·실패·종료) 깨어난다.
  - JSON-RPC 클라이언트는 서버 요청마다 별도 task를 쓰므로 수신 루프를 막지 않는다.
- 새 send를 시작할 때 이전 turn ID를 지운다. 그 사이에 오는 이전 turn의 호출은 위 규칙으로 거절된다.

### D-06 · 답변 블록 수 상한

- `parse_blocks`는 블록을 최대 `MAX_BLOCKS = 500`개까지 만든다. 499개를 넘으면 나머지 줄은 고치지 않은 평문 문단 하나로 둔다.
- 블록 하나가 위젯 하나이고 표·코드도 위젯 하나라서, 위젯 수가 500개로 묶인다. 내용은 버리지 않는다.

### D-07 · 기기 로그인 URL·코드 검증

- 고정 Codex 0.162.1 바이너리에서 기기 로그인 경로 `/codex/device`와 기본 issuer `https://auth.openai.com`을 확인했다.
- `verificationUrl`은 `https://auth.openai.com/` 뒤 경로 문자(영숫자 `/._~-`) 200자 이하만, `userCode`는 영숫자·`-` 4–32자만 받는다.
  - 아니면 화면에 내지 않고 시작된 로그인을 `account/login/cancel`로 풀고 `FAILED`를 돌려준다.
  - 같은 호스트는 C의 브라우저 로그인 검사(`login_browser`)도 허용한다.
- 시험용 가짜 app-server의 기기 로그인 주소를 실제 경로로 바꿨다(`https://auth.openai.com/codex/device`).

## 시험

| 파일 | 추가·변경 |
| --- | --- |
| `test_mcp_ingress.py` | 수집 밖 프레임 상한(경계 포함·파싱 없음·읽기 양), 수집 중에도 상한·인자 검사, 실제 SDK 세션에서 8 MiB를 넘는 `tools/call` 결과가 파싱 전에 호출·서버를 끝냄(재시도 없음). 기존 시험 이름을 `test_listing_limits_do_not_apply_outside_collection`으로(뜻 정정) |
| `test_mcp_http_ingress.py` | JSON·gzip JSON·SSE data·SSE 필드·다른 media·다른 encoding 6가지가 상한에서 멈추고 파싱하지 않음, 경계 길이는 통과 |
| `test_codex_session.py` | 많은 turn ID·큰 완료 메시지로 정지, 24번 연속 turn이 상한에 걸리지 않음, 다른·끝난 turn의 호출과 turn 밖 호출은 Guard에 가지 않음, `turn/start` 응답보다 먼저 온 정상 호출은 기다렸다가 Guard에 감 |
| `test_ui_chat.py` | 3만 줄 블록이 500개로 묶이고 꼬리는 원문대로 남음, 499개는 그대로, 위젯 500개 |
| `test_codex_account.py` | 다른 호스트·http·호스트 접미 위장·줄바꿈·긴 경로·bidi·줄바꿈 코드·짧은/긴 코드 9가지를 표시하지 않고 로그인을 풂 |
| `test_a138_product_startup.py` | `test_tool_response_and_turn_check_store_authority`가 진행 중인 turn 없이 `_tool_call`을 직접 불렀다. D-05로 그 호출은 Guard에 닿지 않아 저장소 정지가 안 일어남. 호출 동안 `_sending`·`_turn_id`를 진행 중인 turn으로 두게 준비만 바꿈(도구 응답·새 turn의 실행 권한 확인이라는 시험 의도는 그대로) |

## 검증

2026-11-05, Windows / Python 3.12.15 / 로컬 `.venv`.

| 확인 | 결과 |
| --- | --- |
| 단위 시험 전체 1차 | 1 실패(위 A138 시험), 5798 통과, 26 건너뜀, 하위 1303 통과 |
| 단위 시험 전체 2차(A138 시험 준비 수정, D-05 시험 추가, 시험 lint 정리 뒤) | 5800 통과, 26 건너뜀, 하위 1303 통과(6분 3초) |
| 계약 시험 `pytest -q tests/contract` | 45 통과 |
| `ruff check .`·`ruff format --check .`(401개) | 통과 |
| `mypy`(145개) | 통과 |
| import-linter | 계약 7개 유지 |
| `bandit -r src -ll -q`(1.9.4) | 0건 |
| 되돌림 8가지(별도 worktree에서 하나씩) | 모두 해당 시험 실패: D-01 상한 끔 4건, D-01 통과 본문만 1건, D-02 상한 끔 2건, D-02 정리 끔 1건, D-05 대조 끔 2건, D-05 대기 끔 1건, D-06 1건, D-07 1건. 복원 뒤 12건 통과 |

- 처음 쓴 D-05는 `turn/start` 응답 전에 온 다른 turn ID 호출을 통과시켰다. 시험이 그것을 잡아 대기를 넣었다.

## 한계

- D-03(turn 전체 기한)은 하지 않았다.
  - 설계에는 호출별 감시 상한(10.4절)만 있다. 시간 값은 계약상 측정 뒤 정하는 항목이다.
  - 값을 잘못 잡으면 정상적인 긴 xhigh turn이 끊긴다.
- D-04(모델용 schema의 비검토 주석)는 하지 않았다. Catalog(B)와 Codex 등록(A) 사이 경계라 DESC-01과 함께 A·B가 정해야 한다.
- D-01
  - 8 MiB는 메시지 하나의 상한이다. SDK가 파싱해서 만드는 객체의 메모리는 그보다 커질 수 있다. 그 다음 단계의 결과 예산(`check_result_budget`)은 그대로다.
  - SDK가 직접 띄우는 stdio(시연 전용)에는 적용되지 않는다.
- D-05: 호출이 `turn/start` 응답을 기다리는 시간은 `turn/start` 요청 시간 상한(30초)에 묶인다. 이 대기는 호출 감시 상한(330초)에 들어가지 않는다.
- D-06: 스트리밍 중 매 조각마다 전체 본문을 다시 파싱하는 비용(최대 65,536자)은 그대로다. 위젯 수만 묶었다.
- D-07: Codex가 기기 로그인 주소나 코드 형식을 바꾸면 기기 로그인이 `FAILED`가 된다. 고정 버전을 올릴 때 다시 확인해야 한다. 실제 `userCode` 형식은 이번에 실측하지 않았다.
- 실제 MCP 서버·실제 codex.exe·실제 창으로는 돌리지 않았다. 모두 가짜 peer·가짜 app-server·offscreen Qt 시험이다.
- B·C 소견(Scanner 보고서 finding 수 상한 없음, A 화면도 모두 위젯으로 만듦)은 PR에 적고 여기서는 다루지 않았다.
