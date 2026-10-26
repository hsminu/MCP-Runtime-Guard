# A74 · Task 자원 준비와 종료 확인 뒤 정리

2026-10-18. A-local TaskResources receipt에 신뢰 Task ID, 구성, 소유 token과 opaque handle을 묶고 SessionRuntime이 준비→구성 재검증→세션 생성→Store 채택 순서를 관리하도록 했다. opaque handle과 token은 repr에서 제외한다. 실제 경로·프로세스 조작이나 B 저장소 구현은 추가하지 않았다.

실패·취소 뒤에는 소유가 확인된 Task 자원만 회수한다. 세션 close 완료와 `confirm_exit`의 정확한 True를 확인한 뒤에만 `cleanup_task`를 호출한다. None 반환을 약속한 종료/정리 callback이 다른 값을 반환하거나 제한 시간 안에 끝나지 않으면 완료로 처리하지 않는다. 정리 미확인은 유지되며 새 Task 생성을 거부하지만 다른 실행 중 Task를 취소하지 않는다. 실제 생성·종료·삭제와 앱 시작의 잔여 폴더 처리는 C의 주입 구현 책임이다.

리뷰에서 발견한 두 경합을 보정했다.

- 취소를 무시한 factory가 아직 진행 중일 때 전체 종료가 자원을 먼저 정리할 수 있었다. 생성 작업의 Task ID를 별도 추적해 전체 정리에서 제외하고, 늦은 반환 뒤 해당 생성 작업의 finally가 회수한다.
- 잘못된 Task ID·중복 token/handle을 반환하면 소유권을 확인할 수 없어 임의 정리를 하지 않지만, 기존 초안은 후속 생성을 계속 허용했다. 이제 정리 미확인 상태로 남기고 추가 준비를 차단한다. 다른 Task 자원을 삭제하지 않는다.

검증: A72와 같은 준비/runtime **45개 통과**(`-W error`). 종료 확인이 False·None·정수인 경우 cleanup 0, 잘못된 cleanup 반환 시 소유 유지, 실패 후 새 준비 0, 다른 Task 동작 유지, 취소 무시 단계의 늦은 반환 회수를 확인했다. 준비 중 생성 작업을 닫을 때 factory 해제 전 confirm/cleanup 0, 해제 후 close→confirm→cleanup 순서를 계수로 검증했다. 루트 GUI 14 확인도 최종 코드에서 통과했다. Ruff·format·mypy 통과.

관련 I6·I7·I9, T43·T46 의도다. 이 계수는 주입된 함수 호출 계수이며 실제 프로세스 종료나 파일 삭제 증거가 아니다. 준비 callback은 유효 receipt를 반환하기 전 부분 자원의 정리를 책임지고, 확인 불가한 자원을 A가 경로 추측으로 삭제하지 않는다. 실제 C API 연결과 B STOP/NOTIFY는 대기한다. PR은 생성하지 않았다.
