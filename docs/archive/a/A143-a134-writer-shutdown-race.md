# A143 · A134 종료 순서의 실제 writer 경합 확인

- 관련: I6, STOP-01, REC-01·REC-05, 개발문서 10.4·11.5절. T41·T42의 공통 정지 경로와 T46의 인증 실패 정지에 대한 A 단위 근거이며 해당 통합 시험의 인수를 뜻하지 않는다.
- PR: 미정

## 한 일

`tests/unit/test_a143_writer_shutdown_race.py`에 `test_shutdown_records_session_stop_before_real_writer_closes`를 추가했다. 제품 코드는 변경하지 않았다.

- 실제 `SQLiteStore`와 B6.13 writer, `assemble_guard`로 조립한 Guard, RUNNING Task를 사용한다. `test_product_assembly`의 Peer·Registry·Profile 도우미, `test_session_runtime`의 Session·준비 도우미를 재사용한다. `make_runtime`은 ObservedStore를 만들고 `stop_one_task`는 가짜 정지 객체의 waiting 신호를 요구하므로 그대로 사용하지 않았다.
- B의 `test_b_registry_writer_integration.py`에 있는 CommitBarrier가 함수 내부 클래스여서, 동일한 COMMIT 직후 바리어를 작은 로컬 클래스로 두고 출처를 주석으로 남겼다. Store·Guard·정지 객체는 대체하지 않는다.
- 무장 후 다른 Task의 create_task COMMIT에서 writer를 멈춘다. 합성 Codex 세션의 인증 실패 → runner.advance → SessionRuntime의 StopChannel → 기록용 BackgroundWork → 실제 Guard 시스템 채널 stop_task로 정지를 시작한다.
- 기록 future가 미완료이고 writer 큐에 아직 시작하지 않은 작업 하나가 있음을 확인한다. threading.Timer(0.2)로 바리어를 풀면서 실제 SessionRuntime.close를 호출한다.
- 종료 성공, 기록 future의 예외 없음(SystemControlError 포함), wait_closed=True, 새 Store로 재조회한 Task의 HALTED를 단언한다. 원래 연결을 닫았음을 확인한 뒤 재개방한다.
- 본 흐름·정리에는 각각 asyncio.timeout(8), committed 대기 3초, writer 바리어 5초, wait_closed 3초, timer join 1초 상한을 둔다. finally에서 release.set, timer 취소·join, runtime·Store 정리를 한다. 넓은 예외 처리나 CancelledError 삼키기는 추가하지 않았다.

## 실제 검증

Windows, Python 3.12.15, Git Bash, worktree `C:/Project/sogaeding/MCP-Runtime-Guard/.cache/a-work/wt-A143`, 브랜치 `wip/a143`에서 실행했다. 원래 checkout의 src·tests·docs는 읽거나 변경하지 않았다.

아래 명령의 공통 환경:

```bash
export PYTHONPATH=src QT_QPA_PLATFORM=offscreen
export TMPDIR="$PWD/.cache/a-work/A143-tmp-0"
export TEMP="$TMPDIR" TMP="$TMPDIR"
# 아래 $PY는 이 실행 파일의 약기다.
PY=/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/python.exe
```

`PYTHONPATH=src $PY -c 'import guard_app; print(guard_app.__file__)'`로 worktree의 src/guard_app/__init__.py를 import함을 확인했다.

```bash
for i in {1..20}; do
  $PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A143-tmp-repeat-$i tests/unit/test_a143_writer_shutdown_race.py || exit 1
  echo "REPEAT $i/20 PASS"
done

$PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A143-tmp-5 tests/unit/test_session_runtime.py tests/unit/test_session_collaborators.py tests/unit/test_product_assembly.py tests/unit/test_b_registry_writer_integration.py tests/unit/test_a143_writer_shutdown_race.py
$PY -m ruff check .
$PY -m ruff format --check .
$PY -m mypy
/c/Project/sogaeding/MCP-Runtime-Guard/.venv/Scripts/lint-imports.exe --config tools/ci/importlinter.ini
git diff --check
```

| 검사 | 결과 |
| --- | --- |
| 새 시험 독립 프로세스 20회 반복 | 20/20 통과, 매회 1 passed, 1.59–2.09초 |
| 지정한 5개 파일 | 55 passed, 34 subtests passed, 5.41초 |
| Ruff check | 통과 |
| Ruff format --check | 290 files already formatted |
| mypy | 114 source files, 오류 없음 |
| import-linter | 7 kept, 0 broken |
| git diff --check | 통과 |

초기 작성 중 Registry에 fingerprint 문자열 대신 ToolSpec을 넘긴 오류 1건을 수정했다. Ruff가 지적한 복합 assert 2개도 분리한 뒤 위 최종 검사를 실행했다. 전체 단위 시험, 별도 contract·통합·공격 시험, Scanner·Semgrep·성능 측정은 미실행이다.

## 옛 순서 대조

각 대조는 session_runtime.py의 원래 바이트를 메모리에 보관하고 임시 치환한 뒤, subprocess로 새 시험을 실행하고 finally에서 원래 바이트를 그대로 썼다. 두 번 모두 바이트 일치와 SHA-256 `e30e92ee5c9575f473870c4d7a9e5795b875c63eccad13f86fbed0580b3504f9`를 확인했다. 최종 git diff에도 제품 코드 차이는 없다.

명령은 `$PY -m pytest -q -p no:cacheprovider --basetemp=.cache/a-work/A143-tmp-<번호> tests/unit/test_a143_writer_shutdown_race.py`였다.

| 임시 회귀 | 번호 | 실제 결과 |
| --- | --- | --- |
| _close_guard의 settle에서 `recorded = await self._records.wait(self._limits.stop_record_s)`만 `recorded = True`로 교체 | 3 | 1 failed(exit 1). SystemControlError=True, 새 DB 조회는 HALTED |
| 위 교체와 함께 `ended = await self._records.wait(self._limits.session_s)`도 `ended = True`로 교체 | 4 | 1 failed(exit 1). SystemControlError=True, 새 DB 조회는 RUNNING |
| 원래 코드 복원 | 반복 1–20 | 모두 통과. 기록 예외 없음, HALTED, 연결 종료 확인 |

첫 대조는 뒤쪽 대기가 남아 Store가 열린 상태여서 HALTED 쓰기는 완료된다. Guard의 시스템 명령은 감사 뒤 전역 정지를 재확인하므로 성공 ACK 대신 SystemControlError로 끝난다. 두 대기를 없애면 Store 닫기가 앞서 큐의 정지 작업이 실패하고 RUNNING으로 남는다. 새 시험은 BackgroundWork가 소비한 예외까지 직접 확인하므로 HALTED만 확인하는 시험보다 첫 회귀도 잡는다. 대조 결과에 예외 원문은 기록하지 않았다.

## 남은 한계

- 세션·준비 콜백·MCP peer·Registry는 합성 도우미다. 실제 Codex, C L1/L2, OS 단일 소유 잠금, 프로세스 강제 종료는 검증하지 않았다. REC-01 근거는 연결 종료 확인까지이며 잠금 해제 순서 전체 인수가 아니다.
- writer 큐와 기록 future의 내부 속성을 관찰하므로 내부 구조 변경 때 시험 조정이 필요하다. 실제 writer·Guard 동작은 그대로 실행한다.
- 정지 없는 Task의 진행 중 submit 커밋, 실제 외부 wire·부작용 계수, bridge 종료 상한과 세션 정리 상한의 관계는 이번 범위 밖이다.
- 0.2초 timer와 5초 바리어의 여유는 이 Windows 환경 20회에서 확인했다. 다른 환경의 지연이나 모든 스케줄을 증명하지 않는다.
- docs/a-status.md는 수정하지 않았다. A134 미완료 줄의 완료 문안은 별도 요청된 A143-report.md에 남긴다. 현재 worktree의 막힌 것 표에는 A134 행이 없어 삭제 대상은 없다.
