# A96 · 등록에 없는 도구가 보이면 서버 차단

2026-10-19. PR #29 B 리뷰 3번이다. 연결 직후 지문 대조(A86)를 B 3차 PR의 `RegistryStore.record_fingerprint_change` 기준에 맞췄다.

## 한 일

- `services/product_assembly.py`의 `live_fingerprints`에서, 아래 경우 그 서버를 이번 실행 동안 PENDING으로 취급한다.
  - 서버의 `tools/list`에 C 등록에 없는 도구가 있을 때
  - 이름이 없거나 같은 이름이 반복된 도구가 있을 때
- 이제 도구 추가도 삭제·정의 변경과 똑같이 정의 변경으로 본다.
- 비교 기준은 C 등록 전체다. 그래서 등록에는 있지만 Profile이 고르지 않은 도구는 막지 않는다.

## 검증

- `test_product_assembly.py`: 등록된 `read`는 일치하고 `list`만 새로 보이는 경우를 넣었다.
  - 서버가 차단된다(`blocked_servers`, PENDING).
  - 모델 도구 목록이 비고, 호출은 `REG_NOT_ACTIVE`로 거부되며 서버로 가는 실제 호출은 0건이다.
- 이 테스트는 수정 전 코드에서 실패하는 것을 확인했다.

## 남은 것

- 실행 중 `notifications/tools/list_changed`를 받았을 때의 처리는 #30(LIST-01 가칭)의 사용자 결정을 기다린다.

관련: I10, T10.
