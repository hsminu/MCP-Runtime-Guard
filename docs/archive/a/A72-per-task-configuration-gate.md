# A72 · 앱 준비 1회와 Task별 구성 검증

2026-10-18. C의 [#21 설정·실행기·경로 회신](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6247401327)을 바탕으로 A 내부 `session_preparation.py`와 SessionRuntime의 주입 연결부를 구현했다. 불변 ConfigurationReceipt/StartupReceipt와 준비·검증 callback 묶음은 A 내부 자료형이며 최종 B DTO나 C API를 확정하지 않는다.

기존 bool L2 callback과 성공 여부만 캐시하는 경로를 제거했다. 앱 공통 준비는 한 번 시도하고, 성공한 구성의 config/executable/model SHA-256 식별자가 L2가 검사한 구성과 일치해야 한다. 누락·실패·잘못된 형식·불일치는 시작을 거부한다. 준비 실패도 같은 앱 안에서 공용 설정을 반복 작성하는 재시도로 이어지지 않는다.

각 Task는 자원 준비 전과 세션 factory 직전에 주입된 검증 함수의 관측값을 다시 대조한다. 구성 변경이나 자원 receipt의 불일치를 발견하면 factory 또는 Store 채택 전에 중단하고, 이미 소유한 자원은 A74 순서로 정리한다. 파일 읽기·해시 계산·설정 쓰기·exe 검증·L2 실행은 직접 구현하지 않았다.

검증: 새 준비/수명 단위 테스트 32개와 기존 runtime 테스트 13개를 합쳐 **45 통과**(`-W error`). 불변 receipt, 준비 1회·Task별 재검증, L2와 구성 불일치, 누락/형식 오류, 준비 중 구성 변경, callback 예외의 고정 코드 변환, 취소를 검증했다. 새 모듈과 runtime의 Ruff·format·mypy 통과.

루트의 실제 bridge·SQLite·offscreen GUI 합성 검증 **14 확인**에서 구성 불일치 시 추가 factory 호출 0, 기존 Task 유지, 정리 미확인 시 신규 Task 거부와 다른 Task 응답을 확인했다. 최종 작업 트리 Ruff 전체, format 143파일, mypy 66소스 통과. 로컬 근거는 ignored `.test-artifacts/a72_gui_qa.py`와 `a72-gui/report.json`이다.

관련 I6·I7·I11, T22·T46 의도의 A 단위 검증이다. hash 식별자의 실제 파일과의 결속은 향후 C 제공 함수의 책임이며 이 검증이 실제 해시 대조·L1/L2/T46 인수를 증명하지 않는다. Profile/정책 로딩·Guard 연결·공통 계약은 계속 B·C 대기다. 기본 제품에는 해당 구현을 주입하지 않아 시작을 거부한다. B·C·공통 설정은 수정하지 않았고 PR은 생성하지 않았다.
