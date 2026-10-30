# A217 · #113 B·C 리뷰 후속

2026-10-30 · `wip/next` · PR #113. 출처: #113 B 리뷰(Neo6424), C 리뷰(hsminu, head `de6dc26`), 사용자 지시("반영하고 회신").
관련: A208(서버 삭제), A216(offscreen 글꼴).

## 한 일

- **B 2번:** `storage/sqlite_store.py`의 `_RECOVERED_WRITES`에서 `delete_server`를 뺐다.
  - A208이 `block_server`를 따라 넣었고, 근거는 기록에 없었다.
  - 삭제는 서버 목록 화면에서만 부른다. 화면은 `product_startup`이 `activate_run`을 마친 뒤 연결되므로, RECOVERED 단계(REC-01)에 삭제하는 경로가 없다.
  - B가 "경로가 없으면 빼 달라"고 했다. B 소유 파일이지만 A208에서 사용자 지시로 A가 고친 줄이라 같은 범위로 되돌린다.
- **C 사소한 것:** `services/registry.py` `delete`의 영어 docstring을 다른 메서드처럼 한국어로 바꿨다.
- **B 3번:** 같은 ID로 다시 등록하면 revision이 1부터 다시 시작한다. 그래서 `REGISTRY_*` 감사를 읽을 때는 `REGISTRY_DELETED`를 등록 생애의 경계로 본다. 이 한 줄을 PR 본문의 계약 초안에 붙였다. A208 기록은 고치지 않는다.
- **C 1번:** 화면은 바꾸지 않았다(아래 측정). A216 뒤에는 CI처럼 글꼴 경로를 주지 않아도 그 시험이 통과한다.

## C 1번 측정

`ServerListPage`를 320×480으로 띄워 쟀다. offscreen, Windows / Python 3.12.15.

| 환경 | 글꼴 수 | 페이지 최소 폭 | 실제 폭 | 차단 버튼 | 삭제 버튼 |
| --- | --- | --- | --- | --- | --- |
| `QT_QPA_FONTDIR` 없음 | 0 | 304 | 328 | 122 | 142 |
| Windows 글꼴 | 218 | 215 | 320 | 80 | 95 |

- 글꼴이 없으면 대체 글자 폭 때문에 버튼 줄이 320을 넘긴다.
- 실제 글꼴에서는 차단·삭제 줄이 들어가고도 페이지 최소 폭이 215라 여유가 105px쯤 남는다.

## 검증

| 확인 | 결과 |
| --- | --- |
| `test_a208_server_delete.py`·`test_store_run_activation.py` | 50 통과 |
| 새 시험 `test_store_refuses_delete_until_the_run_is_active` | RECOVERED에서 삭제가 `StoreError`로 거절되고 행이 남는다. `activate_run` 뒤에는 지워진다 |
| 되돌림: `delete_server`를 `_RECOVERED_WRITES`에 다시 넣음 | 새 시험 실패 |
| 바꾼 파일 `ruff check`·`ruff format --check`, `mypy` | 통과(147개 파일) |

- 전체 시험은 다시 돌리지 않았다. `delete_server`를 부르는 시험은 A208 파일뿐이고, 복구 단계 게이트 시험은 `test_store_run_activation.py`다.
