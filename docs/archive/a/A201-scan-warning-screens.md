# A201 · 등록 화면·활성화 확인·승인 창의 Scanner 경고 표시

2026-10-30 · `claude/a-scanner-desc-546649`(A200 `76dfc3a` 위). PR: 다음 A PR(아직 없음)
관련: A47·A128·A200, C4.13·C4.14·C4.17, B1.9(#106), 2026-10-13·2026-10-15 Scanner 결정. I7·I9·I10, T17·T18·T29.

## 문제

A200으로 검사 결과가 Registry에 남고 `ScanState`가 화면 쪽으로 오지만, 등록 화면과 승인 창은 그것을 보여 주지 않았다. 활성화 확인 창은 "검사 결과 표시는 아직 연결되지 않음"이라고만 했고 늘 `reviewed_scan_id=None`을 보냈다.

## 한 일

- 새 UI 파일 `ui/scan_report.py`.
  - `ScanReportPanel`: 진행 상태 한 줄, Scanner 서비스 오류(`SCAN_QUEUE_FULL`·`SCAN_JOB_TIMEOUT`·`SCAN_RECORD_UNKNOWN`·수집 실패·취소 등)는 검사가 실패했을 때만 보이는 별도 영역, 보고서 진단(`analyzer_diagnostics`)은 분석기 영역과 도구 카드의 "미완료" 줄로 나눴다(B1.9). 키가 없어 LLM이 빠지면 "LLM 검사 안 됨 · YARA 결과만 있음"과 키 저장 안내를 붙인다(C4.14). 도구별 카드는 최고 위험도, 발견 항목(출처·위험도·코드), "LLM 자체 평가(참고)", 모델 설명을 보이고 발견이 없으면 "보고된 위험 없음"이다. 등록 도구에 없는 이름의 발견은 "서버 전체" 카드로 모은다. 지문이 바뀐 옛 보고서는 숨기고 다시 검사 안내만 보인다.
  - 모델 설명(`threat_summary`)은 A47 `UntrustedTextView`(300자, "검증되지 않은 내용" 표시)로만 보이고 로그에 쓰지 않는다. 발견의 `description`은 보이지 않는다. 그 밖의 서버·Scanner 문자열은 `public_display_text`를 거친 `plain_label`이다. 리치 텍스트·링크·색 리터럴은 없다.
  - 모든 경고 화면 끝에 "Scanner 경고는 활성화·승인을 막지 않음. 내용을 보고 직접 판단하세요."를 둔다. 경고는 버튼 상태나 판정을 바꾸지 않는다.
  - `ScanReviewDialog`: 열 때 `scan_status`, "다시 검사"는 `scan`. 명령 ID로 응답을 맞추고, 거부·불명 명령은 고정 문구로 끝낸다. 검사 중에는 다시 검사를 끈다. 닫으면 신호를 끊고 비신뢰 텍스트를 지운다.
  - `ToolScanWarningPanel`: 승인 창에서 호출한 도구의 발견과 그 도구·서버 전체 진단만 보인다. 검사 없이 활성화한 서버는 "이 서버는 검사 결과 없이 활성화됨.", 활성화 때 본 검사를 읽지 못하면 그 사실을 보인다.
- 기존 화면 파일은 끼우는 줄만 바꿨다(`ui-ux-revamp` 세션과 겹침 최소화).
  - `ui/servers.py`: "선택한 서버 검사 결과·설명 검토" 버튼(활성화 버튼과 같은 활성 조건), 활성화 확인 창에 `ScanReportPanel`을 경고 문구 앞에 넣고 첫 줄을 `activation_scan_text`로 바꿈, 확인 창에 실제로 보고서를 보인 경우에만 그 `scan_id`를 `reviewed_scan_id`로 보냄. 차단 확인에는 넣지 않는다.
  - `ui/runtime.py`: 승인 상세(`self.detail`) 바로 뒤에 `ToolScanWarningPanel`, 검토 표시 때 `set_review`, 정리 때 `clear`.
- 스타일 토큰·objectName을 새로 만들지 않았다(`runtimeMessage`·`secondary`·`sectionTitle`만 씀).

## 검증

지정 가상환경 Python, `PYTHONPATH=src QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8`. 전체 시험은 돌리지 않았다. 고정 `ScanReport`와 Qt 신호만 썼고 Scanner·키·dispatch는 없다.

| 실행 | 대상 | 결과 |
| --- | --- | --- |
| Codex 보고 | 아래 8개 파일 | 132 passed |
| A 재실행 | `test_a201_scan_report_ui.py`, `test_a105_activation.py`, `test_ui_sessions.py`, `test_ui_approval_t18.py`, `test_a178_qt_ownership.py`, `test_a_architecture.py`, `test_ui_untrusted_text.py`, `test_ui_icons_style.py` | 132 passed |

- `test_a201_scan_report_ui.py`(38개): 일반 텍스트·분석기·위험도·모델 설명 300자 자름과 닫을 때 지움, 서비스 오류와 진단 분리(코드별), 진행 상태·보고서 없음, 활성화 문구, 활성화 창에서만 붙고 보인 `scan_id`만 보냄, 검사 창의 명령 ID 대응과 신호 해제, 명령 거부·불명, 검사 버튼 활성 조건, 승인 창의 호출 도구 필터와 승인 버튼 유지, 검토 없음·불러오기 실패·진단. Qt 소유권(A178) 검사 포함.
- `ruff check`·`ruff format --check`(바꾼 5개), `mypy src`(142개 소스 파일), `lint-imports`(계약 7개 유지, 0 broken), `git diff --check` 통과. 바꾼 파일 모두 CRLF.

### A 검수에서 다시 잰 것

- 고의 회귀 3종(작업 사본 밖 복사본, `-o pythonpath=<복사본>/src`):
  - `servers.py`가 `reviewed_scan_id=None`을 보내게 하면 1 failed(보인 검사 ID 대신 None).
  - 승인 경고의 도구 필터를 없애면 2 failed(다른 도구의 발견 코드가 보임).
  - 서비스 오류 영역을 실패가 아닐 때도 보이게 하면 12 failed.
- offscreen 렌더링으로 보고서 패널과 승인 경고 패널, `SCAN_QUEUE_FULL` 실패 화면을 눈으로 확인했다. 모델 설명의 URL은 C의 `defang_scanner_text`대로 무력화돼 보였다.

## 남은 것

- C4.13 인수(등록 화면·승인 창 경고)는 C 몫이다. #57 사례 확인도 B1.9대로 C가 한다.
- 모델 설명 상자는 A47 위젯 그대로라 짧은 문장에도 높이를 많이 차지하고, 사용자가 직접 선택·복사할 수 있다(프로그램이 클립보드에 넣지는 않음). 레이아웃 조정은 `ui-ux-revamp` 범위다.
- 서버 상세 창의 기존 `ScanStatusCard`(`scan_snapshot_requested`)는 이번에도 제품에 연결하지 않았다.
- 실제 앱 창(제품 모드)에서 등록 → 검사 → 활성화를 눌러 보지는 않았다. 실제 Cisco 검사 결과의 모양도 확인하지 않았다.
