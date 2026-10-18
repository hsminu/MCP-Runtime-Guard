# A71 · A 단위 테스트 포맷 기준선 정리

2026-10-18. 공통 pyproject를 수정하지 않고 `ruff format --config 'format.exclude=[]'`로 A 소유 제외 대상 11파일을 확인했다. 이 중 8파일(bridge, call_logs, model_client, runner, task_status, ui_keyboard, ui_read_only_pages, ui_responsive_detail 테스트)의 포맷을 정리했다. approval_detail·ui_approvals·ui_overview는 이미 맞아 변경하지 않았다.

담당 에이전트가 11파일 모두 HEAD와 AST가 같음을 확인했고, 루트도 실제 변경 8파일의 AST 동일성을 독립 확인했다. 테스트 의미는 바뀌지 않았다. UI 문구 변경에 필요한 runtime·scan_status 테스트 수정은 A69 범위다.

검증: 관련 13개 테스트 파일 **301 통과·기존 경고 5개**, 소유 UI 두 파일까지 포함한 Ruff 및 제외 해제 format 검사 **15파일 통과**. 통합 작업 트리의 Ruff 전체, 기본 format **139파일**, mypy **64소스**도 통과했다.

A43 전체는 완료가 아니다. 공통 설정의 오래된 제외 경로·타입/린트 예외 제거는 main 대상 변경으로 남기고, 추가 lint refactor는 하지 않았다. 기준선 없이 검사할 때 남는 ASYNC110·PT006·PT007·PT018·S105는 별도 범위다. 소유권 밖 파일을 수정하거나 예외를 늘리지 않았다. 새 동작이나 T/S 인수 주장은 없으며 PR은 생성하지 않았다.
