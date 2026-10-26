# A54 · bridge를 통한 서버 ID 즉시 검사

2026-10-18. main `a41f1d8` 병합 뒤 #20의 6번 회신과 공통 계약의 2026-10-18 결정을 반영했다.

- `services.bridge.valid_server_id`가 B의 `services.catalog_sources.valid_server_id` 결과를 그대로 전달한다. 별도 정책을 복제하거나 입력을 정규화하지 않는다.
- 등록 화면은 기존 앞뒤 공백 제거·길이·숨은 문자·중복 검사를 유지하고 bridge의 결과로 네임스페이스 오류를 즉시 표시한다. `__` 포함 또는 `_`로 끝나는 ID는 초안 준비 버튼과 직접 제출 핸들러 양쪽에서 거부한다. 수정 시 이전 초안과 오류도 갱신한다.
- 순수 형식 검사라 backend 시작 전과 종료 후에도 동작한다. UI는 B 서비스를 직접 import하지 않는다. 이 결과는 등록·활성화 권한이 아니며 C Registry의 최종 검증은 그대로 필요하다.

검증: `test_bridge_server_id.py`, `test_server_add.py`, `test_server_add_folders.py`, `test_ui_keyboard.py`, `test_bridge.py` 집중 검사 **179 passed**. 실제 B 함수와 결과 일치·위임, trim 의미, stdio/HTTP 입력, 즉시 오류·초안 무효화, 정상 ID, 제품 GuardWindow를 확인했다. 최종 전체 Ruff 통과, 포맷 137파일 통과, mypy 63소스 통과. 별도 offscreen GUI에서 잘못된 ID 2종 차단·정상 trim·기존 초안 무효화를 확인하고 1280×800·320×680 화면을 렌더링했다. 프로세스 실행·등록 호출은 하지 않았다.

관련 기준: I7과 T32의 서버 ID/namespace 입력 전제. 실제 Codex 도구 중계 및 Registry 인수를 증명하는 검사는 아니다. B·C 코드, `domain/*`, `ports.py`, 공통 설정·lock은 변경하지 않았다. PR은 생성하지 않았으며 사용자 요청으로 A 브랜치에 커밋·푸시한다.
