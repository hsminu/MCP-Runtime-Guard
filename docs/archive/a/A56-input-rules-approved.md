# A56 · 입력·수집 규칙 승인 확인

2026-10-18. [#19 정정 답변](https://github.com/hsminu/MCP-Runtime-Guard/issues/19#issuecomment-6246859479)과 병합된 [#23](https://github.com/hsminu/MCP-Runtime-Guard/pull/23), main `a41f1d8`의 공통 계약을 확인했다.

승인된 규칙은 API 키 앞뒤 공백 제거, 4자 이하 키 끝자리 숨김, 파일 서버 폴더 규칙 최소 1개, `tools/list` 반복 cursor 중단, 중복 도구 이름 중단이다. 수집 중단은 미완료 경고이며 안전·완료 판정이 아니다. A 현황의 사용자 확인 대기를 승인 완료로 정리했다.

이번 변경은 승인 상태 반영이며 A56 제품 코드를 새로 변경하거나 별도 재검증하지 않았다. 기존 구현 근거는 A48·A49·A09/A10·A57 기록에 있다. 모델 직접 API 입력은 A65에서 제거됐으며 키 입력 규칙은 남아 있는 Scanner 입력에 적용된다. Scanner 자격증명 저장과 Registry 연결은 여전히 C 구현 대기다. 공통 계약은 수정하지 않았고 원격 main의 결정을 가져왔다. 별도 PR은 생성하지 않았다.
