# A163 · #86 후속: 시작할 서버, 설정 문제 표시, Store 조회 연결

2026-10-29 · `wip/next`(main `b14c181` 병합 위, 커밋 `accdc20`·`97d3534`). PR: 아직 없음(로컬 커밋).
관련: #86(B Profile 확인·B6-05 `StoreQueries`), A155·A157·A33–A35, I6·I7·I9, T18.

## 한 일

### 시작할 서버만 시작

- 조립의 스트림 생성·연결·지문 대조·목록 기록을 `LoadedProfile.startable_servers`로만 한다.
  - B Profile 확인이 저장 URL을 거부한 서버(`URL_*`)는 시작하지 않고 스트림도 만들지 않는다.
  - `PROFILE_SERVER_UNAVAILABLE` 확인도 이 서버 집합으로 한다.
- A155의 `ahead`·`_boundary_matches` 경계 비교를 지웠다. #86부터 설정과 등록이 다른 서버(`REG_SETTINGS_MISMATCH`)는 B Catalog가 그 서버의 도구만 숨긴다.
- `_RefusedStream`은 남겼다. B 정규형 확인과 C 정규화가 어긋날 때를 대비한 것이다.
- 새 등록의 목록 수집(`collect_registration_listing`)은 바꾸지 않았다. 요청으로 만든 서버만 다루고 Profile 서버는 다루지 않는다.

### 서버별 설정 문제 표시

등록 행에 B `server_issue`를 싣는다. Profile이 고른 서버만 해당한다. 서버 목록의 Registry 열에는 " · 설정 확인 필요"를 붙이고, 상세에는 "설정 확인" 줄을 둔다. 고정 문구만 보이고 모르는 코드는 그대로 내보이지 않는다.

| 값 | 문구 |
| --- | --- |
| 없음 | 확인된 문제 없음 · Profile에서 고른 서버만 확인함 |
| `URL_*` | 저장된 URL을 쓸 수 없음. 이 서버는 연결하지 않음. |
| `REG_SETTINGS_MISMATCH` | 설정 파일과 등록 내용이 다름. 이 서버의 도구는 숨겨짐. |
| 그 밖 | 설정 확인 필요 |

### 승인 목록·호출 로그를 B Store 조회에 연결(B6-05 제안)

- **bridge 조회 레인(`services/bridge_queries.py`)**
  - 백엔드 루프에서 종류(`approvals`·`calls`·`call`)마다 하나만 읽는다. 기다리는 요청은 가장 새 것만 둔다.
  - Store 오류·시간 초과(5초)·종료는 오류 원문·타입 없이 `failed`로 답한다(A82 완료 콜백 패턴).
  - 닫을 때는 진행 중 읽기를 취소하고 정해진 시간만 기다린다. 끝나지 않으면 종료 코드를 `CLEANUP_UNCONFIRMED`로 남긴다. 읽기는 bindings가 Store를 닫기 전에 끝낸다.
- **bridge 공개 면**
  - `request_query`는 종류와 요청 형식, 정수 토큰을 검사하고 어긋나면 `QUERY_REQUEST_INVALID`를 낸다.
  - 답은 `query_answered`로, 조회 원천 유무는 `query_source_changed`로 알린다. READY가 아니면 바로 실패로 답한다.
  - 제품 bindings는 SQLiteStore를 `StoreQueries`로 넘긴다.
- **화면 연결(`ui/store_views.py`의 `StoreQueryLink`)**
  - READY이고 조회 원천이 있을 때만 두 화면을 연결한다. READY를 벗어나면 끊고 옛 행을 지운다.
  - 현재 요청 토큰과 맞는 답만 반영한다.
- **승인 목록**
  - 행에 `row_revision`을 싣는다. 목록에는 아직 승인·거절 명령이 없어서, 다음 명령의 기대값으로 둔다.
  - "승인 유효 시간 지남" 참고 표시는 Profile TTL 추측 대신 저장된 `expires_at_utc`로 정한다. 그 시각부터 보인다. 상세에는 만료 시각 줄을 둔다.
- **호출 로그**
  - 페이지는 50건씩이고, 검색은 서버로 보낸다.
  - 단계는 B 단계(`DECIDED`·`WAITING_APPROVAL`·`DISPATCHING`·`OBSERVED`·`COMMITTED`·`WITHHELD`)만 쓴다. `DISPATCHED`·`RELEASED`는 지웠다.
  - `COMMITTED`는 "결과 기록 · 성공 여부는 현재 실행 상태로 확인"이고, 성공은 `status`로만 본다.
  - 신뢰 경계 열을 표·CSV·상세에서 지웠다.
  - 상세의 판정 사유에는 기록된 사유를 모두 보인다.
  - 호출 ID 자리에는 요청 ID를 쓴다. Store 행에 Codex 호출 ID가 없다.
  - "승인 불필요"는 판정이 ALLOW·DENY이고 승인 상태가 없을 때만 보인다. ASK인데 승인 상태가 없으면 비워 둔다.
- **두 화면 공통**
  - 검색어·서버 ID가 `[A-Za-z0-9_.:-]{0,64}` 밖이면 보내지 않는다. 대신 "검색어와 서버 ID는 영문·숫자와 _ . : - 만 64자까지 입력하세요."를 보인다.
  - 보낼 때는 대소문자를 그대로 둔다. B는 앞부분 일치이고 대소문자를 구분한다.
  - offset이 10,000을 넘으면 보내지 않는다.
  - 조회 실패는 "불러오지 못함 · 항목이 없다는 뜻이 아님. 새로고침하세요."로 보여 빈 목록과 구분한다.
- **목적지 표시**: ID만 보인다. #76에서는 "destination_id와 Profile에서 만듭니다"라고 했다. 하지만 요청 때의 목적지 등급·이름은 Store에 없다. 지금 Profile로 만들면 옛 행을 잘못 보일 수 있다. 경계 등급 열을 지운 이유와 같다.
- **시연**: `examples/ui_demo.py`는 TTL 상수로 `expires_at`을 만들고 새 단계 코드를 쓴다.

바꾼 파일:

- services: `bridge.py`, `bridge_host.py`, `bridge_types.py`, `bridge_queries.py`(새), `bridge_registration.py`, `product_assembly.py`, `session_runtime.py`
- ui: `approval_presentation.py`, `approvals.py`, `call_logs.py`, `store_views.py`(새), `servers.py`, `models.py`, `window.py`
- `examples/ui_demo.py`와 시험

## 검증

Windows 11, Python 3.12, 로컬 `.venv`, `PYTHONPATH=src`, `QT_QPA_PLATFORM=offscreen`.

- 조회·화면·bridge·시작 묶음 26개 파일: 355 passed + subtests 7.
- 조립을 거치는 나머지 14개 파일: 360 passed + subtests 49. B·C 시험은 돌리기만 했다.
- 첫 커밋 때 등록·조립 묶음: 129 passed.
- 새 시험
  - `test_store_query_link.py` 10개: 조회 레인, bridge 검사, 변환, 화면 연결, 창 연결, 끝나지 않는 읽기
  - `test_call_logs.py`: 연결 모드 1개
  - `test_ui_approvals.py`: 만료 시각 시험 1개(Profile TTL 시험을 대체)
  - `test_a138_product_startup.py`: 제품 bindings에 `queries`가 있는지 단언
- 고친 시험: `test_a138`의 HTTP 시작 시험은 정규형 URL을 저장한다. `…:80/mcp`는 #86 Profile 확인이 거부해 이제 시작하지 않는다.
- 정적 검사: `ruff check`·`ruff format --check`, `mypy` 138개 소스, `lint-imports`(계약 7개 유지), `git diff --check`를 통과했다.
- 전체 단위 시험은 돌리지 않았다.

고의 회귀 24개를 하나씩 넣고 해당 시험 노드만 돌렸다. 24개 모두 해당 시험이 실패했다. 매번 원래 바이트로 되돌리고 SHA-256 일치를 확인했다.

| 고의 회귀 | 잡은 시험 |
| --- | --- |
| 기다리는 요청 중 첫 것을 남김, 종류별 하나 제한을 뺌 | `test_lane_runs_one_read_per_kind_and_keeps_only_the_newest_waiting` |
| 오류를 실패로 보지 않음 | `test_lane_answers_failed_for_errors_timeouts_wrong_requests_and_no_source` |
| 닫을 때 취소하지 않음, 닫은 뒤에도 답함 | `test_lane_close_cancels_running_reads_and_refuses_new_ones` |
| 끝나지 않은 읽기를 종료 코드에 넣지 않음 | `test_bridge_close_reports_a_store_read_that_does_not_end` |
| 토큰 형식 검사를 뺌, READY 전 실패 답을 뺌 | `test_bridge_refuses_malformed_queries_and_answers_failed_before_ready` |
| 조회 원천을 열지 않음, 옛 토큰 답 반영, 연결 뒤 새로고침 안 함, READY를 벗어나도 연결 유지, 승인 검색어 검사를 뺌 | `test_link_fills_both_pages_from_the_store_and_shows_failures_as_unknown` |
| ASK도 승인 불필요로 봄, `expires_at`을 버림, 단계를 미실행으로 바꿈 | `test_store_rows_become_screen_rows_without_a_tier_or_a_guessed_expiry` |
| 서버 ID 형식 검사를 뺌 | `test_screen_queries_follow_b_limits_and_keep_the_search_case` |
| 호출 검색어 검사를 뺌, 연결 중에도 화면에서 다시 거름, 줄어든 마지막 페이지를 다시 묻지 않음 | `test_store_pages_follow_the_current_query_and_refuse_text_b_would_refuse` |
| 만료 시각과 같은 때를 아직 유효로 봄 | `test_waiting_hint_follows_the_stored_expiry_not_the_profile` |
| 제품 bindings에 조회를 넘기지 않음 | `test_settings_build_real_guard_and_own_server_exit` |
| 창이 조회를 연결하지 않음 | `test_window_reads_both_pages_from_the_runtime_store` |
| 조립이 `selected_servers`를 그대로 씀 | `test_product_assembly.py`, `test_mcp_launcher_pipes.py` |

## 남은 한계

- B6-05는 `docs/contracts.md`에서 "요청 초안"이고, B `domain/queries.py`도 합의 전 제안이다. 합의에서 바뀌면 `store_views.py` 변환을 맞춘다.
- 승인 목록에서 승인·거절 명령은 아직 없다. 승인은 대화 화면에서 처리한다. `row_revision`은 실려 있지만 아직 어떤 명령도 보내지 않는다.
- 실제 SQLiteStore로 시험한 것은 bindings 연결까지다(`test_a138`). 실제 행이 채워진 화면은 대역 `StoreQueries`로만 시험했다. B의 SQL 조회 자체는 B `test_store_queries.py`가 다룬다.
- `REG_SETTINGS_MISMATCH` 서버도 아직 시작은 한다. B `startable_servers`가 지금은 `URL_*`만 빼기 때문이다. B 주석에는 A155 이관·C `apply_settings` 연결 뒤 이것도 빼겠다고 되어 있다.
- 사람이 볼 것: 실제 창에서 호출 로그 페이지·검색, 승인 목록의 만료 표시, 서버 상세의 설정 확인 줄.
- B·C 코드와 공통 문서는 바꾸지 않았다.
