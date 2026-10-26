# A91 · 정책 화면과 서버 활성화 도구 허용 설정

2026-10-18. #19 4번(화면은 A, 변경 명령은 B)과 PR #26 결정(활성화 때 도구별 허용 체크박스)의 A 화면 부분이다.

## 한 일

- **`services/policy_view.py`: `PolicyView`.** 불러온 Profile과 C 등록 레코드에서 한 번 만든다.
  - 담는 것: profile·policy_version·승인 유효 시간·taint 모드, 허용·거부 규칙 쌍(B `normalize_access_rules` 정렬 그대로), Profile 서버별 도구 이름·종류.
  - 문서 원문·경로·URL·인자는 담지 않는다.
  - bridge가 READY 때 `policy_changed`로 화면에 보낸다. UI는 bridge에서 다시 내보낸 타입만 import한다. 같은 이유로 A89의 `AccountView`도 bridge에서 가져오도록 바꿨다.
- **정책 화면(`ui/policy.py`)이 자리표시자를 대체했다.**
  - Profile이 없으면 "Guard 정책(Profile) 설정이 아직 연결되지 않았어"라고 보인다.
  - Profile이 있으면 요약(정책 버전은 앞 12자만), 규칙 표, 서버별 "도구 허용 설정" 버튼을 보인다.
- **도구 허용 설정 대화상자(A81).**
  - 기존 규칙이 있는 도구는 그 규칙대로 체크한다.
  - 규칙이 없으면 종류가 정해진 READ/SEND는 체크하고 OTHER는 끈다.
  - 저장은 B의 정책 변경 명령(B3.10)이 있을 때만 켜지고, 이때 "저장하면 실행 중인 Task가 모두 정지돼"를 경고한다. 명령이 없으면 저장을 끄고 그 이유를 보인다.
  - 선택 결과는 `allow_change_requested(server, allowed, denied)`로만 나간다. 실제 저장 연결은 B3.10을 기다린다.

## 검증

- `test_ui_policy.py` 4개: Profile 없음, 규칙 표시, 기본 체크와 저장 불가, 저장 가능 시 경고와 선택 전달.
- `test_product_assembly.py`에 정책 view 생성 1개를 추가했다.
- 전체 단위 테스트 **3,404개 통과, 1 skip, subtest 20**. ruff·format 186파일·mypy 81소스 통과.

## 남은 것

- 실제 규칙 저장과 정책 변경 후 Task 정지는 B3.10을 기다린다.
- 서버 목록 화면에서 활성화하는 흐름은 C Registry 활성화 API를 기다린다.
- 목적지·Profile 편집 화면은 문서에 담당이 정해져 있지 않아 미확정이다.

관련: I2·I6·I9, T18.
