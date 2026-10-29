# A161 · Windows 앱 가상 경로의 L2 실패 재현

2026-10-29 · A · 관련 I6·I11, T22·T39·T40, A151. C 구현 변경 없음.

## 확인한 원인

- Codex 데스크톱에서 시작한 제품 `main()`의 기본 앱 폴더는 논리적으로 `%LOCALAPPDATA%/MCPRuntimeGuard`다. 이 실행환경에서 `Path.resolve()`는 `AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/MCPRuntimeGuard`를 반환했다.
- 실제 고정 Codex 0.162.1은 config 층의 파일과 `model_catalog_json`을 물리 경로로 보고했다. C `codex_l2._check_config()`는 논리 경로와 Path 문자열 동등성을 비교해 같은 파일을 다른 것으로 보았다.
- 원문 config 대신 층 종류·키 이름·경로 일치 bool만 수집했다. user config 경로와 model catalog 경로는 직접 비교 False, 양쪽 resolve 후 비교 True였다. 다른 L1 값 불일치·추가 MCP·profile·별도 지시문은 없었다.
- 기본 경로 두 회 모두 `L2_CONFIG_LAYERS_UNEXPECTED`, `L2_CONFIG_MISMATCH`로 실패했다. Task 시작 버튼은 비활성이고 설정 UI만 READY가 됐다. 이를 하네스 성공으로 세지 않았다.
- 같은 물리 폴더를 기존 테스트 설정 `CodexProcessPreparation(root=app_root().resolve())`로 넘긴 실행은 L2를 통과했다. 평가·결과·모델 제공자·자격증명 저장·launcher를 대체하지 않았다. 제품 파일의 기본 경로 동작은 수정하지 않았다.

## 실제 측정

`perf_counter()`로 제품 `main()` 진입→bridge READY, 실제 `run_l2` 진입→반환, bridge CLOSING→main 반환을 쟀다. 프로세스의 Python import 이전 시간은 제외되고 관찰 JSON 저장 비용은 포함된다. 기본 경로의 READY는 설정 UI 준비만 뜻한다.

| 실행 | 로그인 저장 상태 | 시작(초) | L2(초) | 종료(초) | L2 |
| --- | --- | ---: | ---: | ---: | --- |
| 기본 경로 1 | 유효 API 키 | 6.172872 | 4.751429 | 0.079066 | 실패 |
| 기본 경로 2 | 유효 API 키 | 7.964805 | 5.109168 | 0.244867 | 실패 |
| 정규 경로 1 | 유효 API 키 | 7.094353 | 5.409826 | 0.215173 | 통과 |

완료된 세 실행 모두 exit code 0, 종료 후 Task 폴더 0개였다. 정규 경로 1은 이후 실제 모델 `OK` 응답까지 확인했다. 같은 실행 후반의 로그아웃·틀린 키 로그인은 별도 시나리오이므로 종료 시 자격증명 존재를 정리 실패로 해석하지 않는다.

## 근거와 남은 것

- [안전한 관찰 JSON](../evidence/A161-desktop-verification.json): 위 실행의 시간·고정 실패 코드·프로세스 종료와 경로 비교 bool, API 오류 형식, 화면 관찰 요약. 마지막 실행은 스냅샷 당시 브라우저 인증 대기 중이므로 `main_return=null`이다. 완료로 세지 않았다.
- 관찰 래퍼는 `.cache/a-work/desktop-verification-20261029/observe_product.py`에 있다. 실제 함수 반환값을 그대로 넘기고 UI 입력·전체 RPC·예외 원문·인증정보를 기록하지 않는다. 실제 UI는 Computer Use로 조작했다.
- 사용자는 **C 수정은 남기고 검증만 계속**하도록 결정했다. C의 경로 비교 수정과 기본 제품 시작 재인수는 미완료다. 다른 파일을 같은 것으로 받아들이지 않는 회귀 검증도 C 범위다.
- 일반 Windows 터미널·설치판에서도 같은 경로 가상화가 발생하는지는 미확인이다. 이 환경의 실패를 모든 배포 환경에 일반화하지 않는다.
- API 로그인 상태 측정이며 ChatGPT 브라우저 로그인 상태 측정과 T43 전체 인수는 이 기록에 포함하지 않는다.

PR: 미생성.
