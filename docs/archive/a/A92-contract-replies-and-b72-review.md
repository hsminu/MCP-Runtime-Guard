# A92 · B·C 계약 회신, B7.2 Guard·Catalog 리뷰, Codex 이름 규칙 충돌 알림

2026-10-18. A 몫의 회신·리뷰를 이슈와 PR에 올렸다(게시 전 사용자 확인). 기준은 A 브랜치(main `139e5a0` 병합 상태)다.

## 한 일

- **B6-01–06 회신** ([#20](https://github.com/hsminu/MCP-Runtime-Guard/issues/20#issuecomment-6251208377)): `docs/archive/ac-contract-review-request.md` 3절에 대한 A의 동의와 수정 요청. API 이름·필드는 B 초안을 따르고 A가 실제로 소비할 값만 적었다.
- **ACCESS-01·AGT-04 동의** ([#20](https://github.com/hsminu/MCP-Runtime-Guard/issues/20#issuecomment-6251208623)): 3인 합의 중 A 몫. 동기 `AccessChecker`는 A86의 `AccessRulesChecker`로 연결돼 있다.
- **B7.2 리뷰** ([#20](https://github.com/hsminu/MCP-Runtime-Guard/issues/20#issuecomment-6251208872)): `services/guard.py`·`catalog.py`·`catalog_sources.py`·`catalog_schema.py`를 Guard 우회 관점에서 봤다. 바로 우회·권한 상승으로 이어지는 결함은 찾지 못했다. 중 3건을 올렸다(Windows 경로 별칭 분류, 예약 전 `CancelledError` 전역 정지, 스키마 정규식이 이벤트 루프를 막음). A 쪽 조치는 A94에 있다.
- **C1.9 관측 지점·C 확인 사항** ([#21](https://github.com/hsminu/MCP-Runtime-Guard/issues/21#issuecomment-6251209139)): `SessionObservations`가 노출하는 값을 C 관측 지점과 맞췄다. 같은 댓글 2절로 contracts 2026-10-17 "도구 이름 제한 없음"과 Codex `dynamicTools` 이름 규칙(`^[a-zA-Z0-9_-]+$`)이 충돌한다고 알렸다.
- **PR #28 리뷰** ([리뷰](https://github.com/hsminu/MCP-Runtime-Guard/pull/28#pullrequestreview-5563441075)): NOTIFY-01·STOP-01·B3.10을 A 세션 관리자·Guard 중계·정책 화면에 연결하는 기준으로 봤다. A 쪽 병합 차단 문제는 없고, domain·ports 추가 형태에 동의했다.

## 검증

- B7.2 리뷰: 관련 단위 테스트 6개 파일 399개 통과. 예약 전 취소 전역 정지는 재현 스크립트로 확인했다(Task 2 `Denied INTERNAL_ERROR`, tools/call 0).
- 회신 내용은 A 브랜치 코드와 대조했다. 공통 문서와 B·C 소유 코드는 바꾸지 않았다.

## 남은 것

- B 회신(2026-10-18 PR #28): 1·2·4번 수용. 2·4번은 B 3차 PR에 들어온다. 3번 `LiveCheckedRegistry` 조건과 실행 중 재대조 여부는 PR #28 댓글로 확인 중이다.
- 이름 규칙 충돌은 사용자 결정을 기다린다.
- B6-01–06·ACCESS-01·AGT-04의 B·C 쪽 동의는 미확정이다.

관련: I2·I4·I5·I6·I10, T33.
