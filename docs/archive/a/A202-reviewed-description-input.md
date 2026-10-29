# A202 · PENDING 서버의 도구 설명 검토·검토된 설명 저장(DESC-01)

2026-10-30 · `claude/a-scanner-desc-546649`(A201 `0d29786` 위). PR: 다음 A PR(아직 없음)
관련: A47·A200·A201, B3.8·B3.13(#106), DESC-01. I7·I9, 개발문서 10.4절.

## 문제

B3.8로 `ToolSpec.reviewed_description`과 Catalog 연결, C `ServerRegistry.save_reviewed_descriptions`가 있었지만 A 화면에서 값을 넣을 길이 없었다. 그래서 모델에 가는 도구 설명은 늘 비어 있었다. B3.13(#106 확정)은 PENDING 서버에 저장 → 재활성화 → 재시작 → 새 세션 적용, 원격 설명은 따로 영속화하지 않음, 화면 연결 전 빈 설명을 정했다.

## 한 일

- `RegistrationScans`가 검사에 쓰는 도구 목록(등록 때 받은 것 또는 수동 검사가 새로 받은 것)에서, 검사를 부르기 전에 도구 이름 → 지문과 이름 → 서버 설명을 메모리에만 둔다. `server_descriptions`는 그 지문이 지금 등록과 같을 때만 사본을 준다. 저장소·로그로 내보내지 않고, 앱 종료(`aclose`) 때 비운다. 목록 모양이 틀리면 설명을 두지 않는다.
- 등록 명령에 `descriptions_preview`·`descriptions_save`를 더했다.
  - 미리보기: PENDING이 아니면 `REG_DESCRIPTIONS_UNAVAILABLE`. 행은 등록 도구 순서의 `DescriptionRow(이름, 서버 설명|None, 저장된 검토 설명)`과 현재 `row_revision`이다. 이번 실행에서 받은 목록이 없으면 서버 설명은 None이다.
  - 저장: 모양이 틀리거나 이름이 겹치거나 등록에 없는 도구면 `REG_DESCRIPTIONS_INVALID`, `ReviewedDescription`(2,000 code point, LF·TAB만 허용) 위반이면 `REG_DESCRIPTION_INVALID`, PENDING이 아니거나 revision이 다르면 `REG_DESCRIPTIONS_STALE`. 모두 쓰기 전에 거른다. Store의 CAS·`REG_NOT_ACTIVE` 거절과 `RegistryError`도 STALE이다. 그 밖의 저장 실패·취소는 결과 불명으로 보고 이번 실행의 등록 쓰기를 막는다(기존 `written`/`finally` 방식, 넓은 except 없음, 취소는 그대로 전파). 성공하면 `REG_DESCRIPTIONS_SAVED`와 목록 행.
  - 검사 함수 `reviewed_description_issue`는 domain `ReviewedDescription`의 `TypeAdapter`로 검사하고 고정 코드만 돌려준다. 값을 자르거나 바꾸지 않는다.
- 새 UI 파일 `ui/reviewed_descriptions.py`의 `DescriptionReviewDialog`. 검사 결과 창(A201)의 "설명 검토" 버튼으로 연다.
  - 도구마다 서버가 보낸 설명을 `UntrustedTextView`(4,000자, 검증되지 않은 내용)로 보여 준다. 이 상자는 선택·문맥 메뉴를 꺼서 복사할 수 없다. 받은 목록이 없으면 다시 검사 안내를, 서버 설명이 비었으면 일반 라벨을 보인다.
  - "모델에 보낼 설명" 편집 칸은 저장된 검토 설명으로만 채운다. 서버 설명을 자동으로 넣거나 복사하는 버튼은 없다. 글자 수와 문제(2,000자 초과, 쓸 수 없는 문자)를 바로 보이고, 문제가 있으면 저장을 끈다.
  - 저장 뒤 문구: "검토한 설명을 저장함. 이 서버를 활성화하고 앱을 다시 시작하면 새 세션부터 쓰임." 실행 중 세션은 바뀌지 않는다. 미리보기 실패와 저장 결과 불명은 문구를 나눴다.
  - 닫으면 신호를 끊고 서버 설명 상자와 편집 칸을 비운다.
- 설명 저장은 도구 지문을 바꾸지 않으므로 앞서 기록한 검사 ID는 그대로다. 활성화는 저장 뒤 새 revision으로 다시 미리보기해서 한다.
- 기존 화면 파일(`servers.py`·`runtime.py`)은 이번에 바꾸지 않았다.

## 검증

지정 가상환경 Python, `PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8`. 전체 시험은 돌리지 않았다. 실제 SQLite·Registry·설정 파일과 가짜 수집·가짜 검사기를 썼다. 실제 Scanner·모델 호출, 실제 앱 재시작은 없다.

| 실행 | 대상 | 결과 |
| --- | --- | --- |
| Codex 보고(본 구현) | `test_a202_reviewed_descriptions.py`, `test_a200_registration_scans.py`, `test_a201_scan_report_ui.py`, `test_bridge_registration.py`, `test_reviewed_descriptions.py`, `test_a_architecture.py`, `test_a178_qt_ownership.py` | 169 passed |
| A 재실행 1 | 같은 7개 파일 | 169 passed |
| Codex 보고(문구 수정 뒤) | `test_a202`·`test_a201`·`test_a178`·`test_a_architecture` | 106 passed |
| A 재실행 2(문구 수정 뒤) | 위 7개 + `test_a105_activation.py`, `test_ui_untrusted_text.py`, `test_ui_icons_style.py` | 207 passed |

- `test_a202_reviewed_descriptions.py`: 등록·자동 검사 뒤 미리보기(서버 설명은 그대로, `repr`에 안 나옴) → 일부 저장(넘기지 않은 도구 유지, revision 증가, 지문·검사 ID 그대로) → 새 revision으로 활성화(`reviewed_scan_id` 유지) → B `Catalog.tool_views`의 설명이 검토값이고 서버 문구가 없음. 거부 9종(2,001자·CR·U+2028·모르는 도구·중복·list·list 쌍·짝 없음·문자열 아님)과 STALE·ACTIVE·없는 서버는 쓰기 0. 새 실행(캐시 없음)에서 서버 설명 None·검토값 유지. 지문이 다른 목록의 설명은 주지 않음. 검사 전에 캐시. 알려진 거절은 STALE, 그 밖의 저장 실패·취소는 이후 쓰기 차단과 취소 전파. 검사 함수(빈 값·LF·TAB·한글 통과, 2,001자·CR·NUL·U+2028·U+202E 거부). 창: 일반 텍스트, 편집 칸에 서버 설명 없음, 2,001자에서 저장 꺼짐, 여러 줄 저장의 줄바꿈이 LF, 결과 문구·재시도 규칙, 명령 실패, 미리보기 실패 문구, 검사 창의 버튼, bridge 허용 action.
- `ruff check`·`ruff format --check`(바꾼 7개), `mypy src`(143개 소스 파일), `lint-imports`(계약 7개 유지, 0 broken), `git diff --check` 통과. 바꾼 파일 모두 CRLF.

### A 검수에서 다시 잰 것

- Codex 첫 결과에서 두 가지를 고치게 했다. 미리보기가 실패해도 "저장 결과를 확인하지 못함"이라고 나왔고, 빈 서버 설명에 앱 문구 "서버가 보낸 설명 없음"을 비신뢰 상자에 넣어 "검증되지 않은 내용"으로 보였다.
- 고의 회귀(작업 사본 밖 복사본, `-o pythonpath=<복사본>/src`, 창 시험 제외):
  - `server_descriptions`의 지문 대조를 빼면 1 failed(지문이 다른 목록의 설명이 나옴).
  - 저장 전 설명 검사를 건너뛰면 3 failed(2,001자·CR·U+2028이 저장소 쓰기까지 감).
- offscreen 렌더링으로 창을 확인했다. 서버 설명의 `<b>x</b>`가 글자 그대로 보였고, 편집 칸은 저장값으로만 찼으며, 2,001자에서 저장이 꺼졌다.

## 남은 것

- 설명은 PENDING 서버에서만 고친다(B3.13). ACTIVE 서버는 차단 → 재검토 흐름이 있어야 고칠 수 있다.
- 서버 설명은 이번 실행에서 검사를 돌린 목록에서만 보인다. 재시작 뒤에는 검사 결과 창에서 다시 검사해야 보인다(원격 설명 비영속, B3.13).
- 저장소가 알려진 사유 밖의 `StoreError`(예: `INVALID_ARGUMENTS`)를 내면 결과 불명으로 처리해 이번 실행의 등록 쓰기를 막는다. 앞단 검사로 그 경우는 거의 오지 않는다.
- 실제 앱에서 저장 → 활성화 → 재시작 → 새 Codex 세션의 도구 설명을 눈으로 확인하지는 않았다. Catalog 투영까지만 시험했다.
