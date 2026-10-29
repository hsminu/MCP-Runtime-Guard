# A152 · 현황·승인 화면 렌더링 정리

2026-10-29. `wip/a152`, 별도 worktree `.cache/a-work/wt-A152`에서 작업했다. 동작 유지 리팩터링이며 계약 변경은 없다. PR: 미정.

## 한 일

- `ui/overview.py`에는 페이지 조립, snapshot 적용, 페이징과 포커스 복원을 남겼다. 행·차트 구현은 `overview_rows.py`, 미해결 attention 선택·서버 우선순위·연결 서버 선택은 Qt 없는 `overview_presentation.py`로 분리했다.
- attention 필터·중복 제거는 새 snapshot 적용 때 한 번 계산한다. 이전 결과는 비교에, 새 결과는 렌더링과 페이지 이동에 재사용한다. 같은 ID의 값 변경과 ERROR 전환은 최신 결과를 적용한다. READY와 STALE의 표시 차이, STALE attention이 서버 우선순위를 바꾸지 않는 규칙, 중복 attention ID의 서버 우선순위 의미도 유지했다.
- `ui/approvals.py`에는 query·페이지 검증 호출·선택·응답 적용 순서를 남겼다. 기존 값 타입과 순수 표시/검증 함수는 `approval_presentation.py`, 표 셀 생성과 상태 적용은 `approval_rows.py`로 나눴다. 표는 기존 QTableWidget의 작은 하위 클래스다.
- 원래 정의돼 있던 공개 클래스·함수·상수와 호환용 비공개 helper를 기존 모듈에서도 다시 내보낸다. signal 이름과 payload, 문구, 정렬·필터, 현황 5개/승인 6개 페이지 크기, 포커스 복원과 승인 선택 처리 순서를 유지했다.
- 행 재사용은 적용하지 않았다. 현황은 행 교체 후 즉시/지연 포커스 복원과 링크 연결을 수행하고, 승인 표는 삭제/선택 과정의 Qt 시그널로 선택 사본을 갱신한다. 이를 재사용으로 바꿔도 모든 시그널 순서와 포커스 동작이 같다는 근거는 이번 시험만으로 충분하지 않다. 기존 교체 순서를 유지하고 계산 중복만 줄였다.
- 승인 사본/A131·비신뢰 텍스트/A47의 표시 구현은 바꾸지 않았다. UI에서 security나 저장소·transport를 직접 가져오지 않는다.
- 새 단위 시험 `test_ui_render_projection.py` 5개를 추가했다. 기존 T18 시험의 `DisplayBridge`에는 현재 REC-04 시그널 2개를 보완했다. 승인 자료를 받지 않는 숨겨진 복구 본문만 빈 값·숨김을 단언하고 승인 payload 검사에서 제외했다. 승인 본문의 검사와 외부 열기/HTTP 계수 단언은 그대로다.

## 측정

Windows, Python 3.12.15, 기존 가상환경, `QT_QPA_PLATFORM=offscreen`. **이 PC에서 전후 한 번씩 측정한 값**이다. 통계적 성능 개선 근거가 아니다.

스크립트와 원시 출력은 worktree의 `.cache/a-work/A152-benchmark.py`, `A152-before.json`, `A152-after.json`에 있다. 스크립트는 변경하지 않고 전후 같은 방법으로 실행했다.

- 각 경우 초기 페이지 생성/show 뒤 980×800에서 10번 갱신했다. snapshot 생성 비용과 최초 페이지 생성은 제외했다. `processEvents()`와 `grab()`으로 레이아웃·offscreen paint를 포함한다.
- 현황은 각 surface에 합성 3개/1,000개를 공급하고 서버 surface 시각을 바꿔 갱신한다. 표시되는 행은 현행 페이지 크기로 제한된다.
- 승인은 총 3건/1,000건인 합성 backend page를 공급한다. 한 페이지는 실제 계약대로 3행/6행이다. 1,000행을 한 화면에 넣는 실험이 아니다.
- 만든 QWidget 수는 각 갱신 전후 `QApplication.allWidgets()`의 객체 차이를 세고 이전 wrapper를 유지해 ID 재사용을 막았다. deferred delete는 계수 뒤 처리했다. 승인 표의 QTableWidgetItem은 QWidget이 아니다. 표 항목 수는 `갱신 횟수 × 행 × 6열`로 계산한 보조 값이다.

| 화면 / 원본 행 또는 총건수 | 전 10회 합계 ms | 후 10회 합계 ms | 만든 QWidget 전 → 후 | 표 항목 계산 전 → 후 |
| --- | ---: | ---: | ---: | ---: |
| 현황 / 3 | 78.862 | 78.223 | 420 → 420 | 해당 없음 |
| 승인 / 3 | 31.986 | 31.666 | 0 → 0 | 180 → 180 |
| 현황 / 1,000 | 124.597 | 124.757 | 700 → 700 | 해당 없음 |
| 승인 / 1,000 | 40.033 | 41.493 | 0 → 0 | 360 → 360 |

위젯 수는 같고 시간 차이는 작다. 성능 향상으로 결론 내리지 않는다. attention 계산 횟수 감소는 별도 시험에서 직접 센다.

## 실제 검증

모든 명령은 worktree에서 실행했다. 아래 `A152_PY`는 실제 사용한 가상환경 경로의 약칭이다.

```bash
cd /c/Project/sogaeding/MCP-Runtime-Guard/.cache/a-work/wt-A152
A152_PY=/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe
export PYTHONPATH=src QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8
mkdir -p .cache/a-work/A152-tmp-env
export TMPDIR=$PWD/.cache/a-work/A152-tmp-env
export TEMP=$TMPDIR TMP=$TMPDIR
```

처음 `PYTHONPATH=src`로 `import guard_app; print(guard_app.__file__)`을 실행해 worktree의 `src/guard_app/__init__.py`를 확인했다. 초반 pytest 두 실행은 `--basetemp`만 지정했고 이후에는 위 환경 변수도 지정했다.

```bash
# 코드 수정 전/후 각각 한 번 실행
$A152_PY .cache/a-work/A152-benchmark.py > .cache/a-work/A152-before.json
$A152_PY .cache/a-work/A152-benchmark.py > .cache/a-work/A152-after.json

# 최종 관련 묶음: 82 passed
$A152_PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A152-tmp-5 \
  tests/unit/test_ui_render_projection.py tests/unit/test_ui_overview.py \
  tests/unit/test_ui_approvals.py tests/unit/test_ui_demo.py \
  tests/unit/test_ui_display_escaping.py tests/unit/test_ui_keyboard.py \
  tests/unit/test_ui_approval_t18.py tests/unit/test_ui_read_only_pages.py \
  tests/unit/test_ui_responsive_detail.py tests/unit/test_ui_icons_style.py \
  tests/unit/test_a_architecture.py

# A131 사본, 모델 답, 승인 상세: 8 passed
$A152_PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A152-tmp-6 \
  tests/unit/test_ui_sessions.py::test_review_copy_puts_each_field_on_its_own_line_and_values_cannot_fake_one \
  tests/unit/test_ui_sessions.py::test_conversation_is_plain_text_and_routes_by_task_and_epoch_without_switching \
  tests/unit/test_approval_detail.py

# A47 일반 텍스트 표시: 4 passed
$A152_PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A152-tmp-7 \
  tests/unit/test_ui_untrusted_text.py

$A152_PY .cache/a-work/A152-copy-audit.py
$A152_PY -m mypy
/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini
git diff --check
```

- `rg -l 'overview|approvals' tests`, 이어서 `guard_app.ui.(overview|approvals)|overview\.`와 이동 심볼/monkeypatch 검색으로 직접 UI 사용 시험 8개 파일을 골랐다. 백엔드 approvals만 쓰는 B·C 시험은 대상이 아니다.
- 문구·copy·wording·audit 검색으로 기존 `test_ui_icons_style.py`의 소스 문구/스타일 감사를 실행했다. 별도 하세요체/음슴체 전용 감사 시험은 찾지 못해, 추가로 AST의 한국어 문자열 멀티셋을 비교했다(`A152-copy-audit.py`). 독스트링을 뺀 136개 출현이 전후 동일했다.
- 최종 pytest 합계 94개 통과. T18의 외부 URL/loopback 계수 단언 포함. UI 단위 검증이며 dispatch·wire·수신 부작용의 독립 인수는 이번에 실행하지 않았다.
- mypy: 122 source files 성공. import-linter: 7 kept, 0 broken.
- 변경한 제품 6개 파일과 시험 2개 파일에 `ruff check`·`ruff format --check` 통과(8 files already formatted). 명령 대상: `overview.py`, `overview_rows.py`, `overview_presentation.py`, `approvals.py`, `approval_rows.py`, `approval_presentation.py`, `test_ui_render_projection.py`, `test_ui_approval_t18.py`. 제품은 `src/guard_app/ui/`, 시험은 `tests/unit/` 아래다. `git diff --check` 통과.
- 초기 시험 실패: 새 시험의 목록 줄바꿈 기대를 기존 공개 표시 규칙(`[U+000A]`)에 맞췄다. 제품 표시 규칙은 바꾸지 않았다.
- 기존 T18 실패: `DisplayBridge.recovery_changed` 누락을 `git show HEAD:`의 원래 두 제품 파일로 되돌린 대조에서도 재현했다. 시험 대역 보완 뒤 같은 원래 제품 코드에서도 T18 통과를 확인했다. 대조 후 제품 파일은 바이트 단위 복원했다.

## 회귀 대조

`$A152_PY .cache/a-work/A152-checks.py mutations`로 각 변형에 해당 새 시험 하나를 실행했다. 각 프로세스 상한 90초. 모두 timeout/import 실패가 아닌 단언 실패(exit 1)였다. 매번 `finally`에서 원래 바이트로 복원하고 동일성을 확인했다. 로그는 worktree `.cache/a-work/A152-<이름>.log`다.

| 이름 | 의도적 회귀 | 검출 |
| --- | --- | --- |
| order | attention 결과 순서 뒤집기 | 선택 결과 순서 단언 실패 |
| filter | resolved 필터 제거 | 완료 항목 포함으로 실패 |
| connection | 연결 서버 필터 제거 | 미연결 서버 포함으로 실패 |
| stale | 새 attention 대신 이전 계산 결과 사용 | 계산 횟수 단언 실패 |
| ttl | 승인 대기 시간 참고 조건 뒤집기 | 참고 상태 문구 누락으로 실패 |
| reuse | 같은 승인 ID면 값 변경도 갱신 생략 | `old`가 남아 최신 셀 값 단언 실패 |
| rich | 추출한 현황 행 라벨에 RichText 적용 | PlainText 단언 실패 |

새 시험 5개 모두 최소 한 회귀로 검출력을 확인했다. 승인 대기 시간 표시는 참고 정보이며 만료 판정/승인 권한으로 쓰지 않는다.

## 남은 제안과 한계

- C UI 리뷰: 포커스 복원, query/선택 이벤트, A47·A131/T18 유지 확인. 아직 요청/수신하지 않았다. B 계약 변경 요청은 없다.
- 같은 key의 행 재사용은 별도 단계에서 링크 payload, 값 변경, 삭제/재정렬, 선택 시그널 순서, 스크롤/키보드 포커스를 대조한 뒤 검토한다. 이번 측정만으로 필요하다고 결론 내리지 않는다.
- 목록 가상화·페이지 크기 변경은 동작 변경이므로 별도 제안이다. 적용하지 않았다.
- 실제 화면 DPI·보조 기술·실제 backend/모델 실행은 재검증하지 않았다. 측정은 한 PC의 합성 자료이며 backend 대기나 최초 생성 비용을 대표하지 않는다.
- 원래 checkout의 `src/`·`tests/`·`docs/`에는 접근하지 않았다. 사용자 지정 파일 외에는 쓰지 않았고 Git add/commit/push도 하지 않았다. `a-status.md`는 사용자가 합치도록 보고에 문안만 남겼다.

관련: I1(계층), I5(확정 projection 표시), I9(일반 텍스트·로그 비노출), T08·T18·S05, 설계 0.4·0.5·2·12.4, A47·A131. S05 관련 표시는 단위/T18 계수로 확인했으며 독립 공격 인수 완료를 뜻하지 않는다. `domain`·`ports` 계약 변경 없음.
