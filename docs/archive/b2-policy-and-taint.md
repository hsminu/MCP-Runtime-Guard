# B2 순수 정책과 등급 누적

기준은 기술설계서 3.4·4.1·5·6.1절과 B 작업 문서의 B2 체크리스트다.
`feat/b-security-core`에서 B1 모델·포트 초안을 그대로 사용해 순수 정책과
READ 등급 병합을 구현했다. A·C의 계약 합의와 C의 담당 리뷰는 아직 미확정이다.

## 구현한 함수

| 함수 | 동작 |
| --- | --- |
| `core.matrix.judge_boundary(label, boundary)` | 수신 등급 상한을 먼저 검사하고 등록된 tier의 결정 행렬 적용 |
| `core.policy.evaluate(inp)` | 전제조건 검사 후 서버·SEND 목적지·AGT 중 가장 엄격한 판정 합성 |
| `core.policy.effective_label(state, mode="accumulate")` | 누적 최고 등급, 직전 READ 등급, PUBLIC 비교값 선택 |
| `core.taint.merge(state, observed, source_id, seq)` | READ 관측의 최고 등급·직전 등급·미분류·승격 근거 갱신 |

core는 내부 core 함수, domain, 표준 라이브러리만 참조한다. 시간·난수·외부 I/O가
없으며 입력 모델을 변경하지 않는다. 판정 함수는 저장·호출·승인 소비를 수행하지 않는다.

## 경계와 AGT 합성

READ도 인자를 MCP 서버에 전달하므로 서버 경계를 판정한다. SEND는 서버와 최종 목적지
두 경계를 판정한다. AGT 허용은 ALLOW로, 거절·오류는 DENY로 합성한다.
`allowed=True, error=True`도 DENY다. AGT의 원문 reason은 결과 사유로 복사하지 않는다.

- PUBLIC은 수신 상한을 통과한 모든 tier에서 ALLOW다.
- INTERNAL은 내부에서 ALLOW, 외부에서 ASK다.
- CONFIDENTIAL은 수신 권한이 충분한 내부에서 ALLOW다.
- 외부 경계는 B1 모델에서 max_label을 INTERNAL 이하로 제한한다. 따라서 기밀의
  외부 판정은 행렬에 도달하기 전에 `LABEL_EXCEEDS_BOUNDARY`로 DENY된다.
- 내부 대상도 max_label이 부족하면 DENY다.

최종 판정과 같은 강도의 경계 사유를 서버 → 목적지 순서로 보존하고, AGT 거부가 있으면
고정 `AGT_DENY` 또는 `AGT_ERROR`를 추가한다. 같은 코드가 두 경계에서 발생해도 각각
남기며, `verdicts`로 해당 boundary_id와 모든 경계 판정을 확인할 수 있다.

전제조건은 등록 ACTIVE, 지문 일치, 분류된 Task, phase별 허용 상태, 정책 버전 일치,
SEND 목적지 존재, 지원하는 도구 종류다. 위반 시 해당 사유들을 함께 반환하고 경계
판정은 하지 않는다. `submit`은 RUNNING, `approval_recheck`는 WAITING_APPROVAL만
통과한다. OTHER 거부는 4.1·5.5절의 지원 도구 기준을 정책에서도 적용한 것이다.
미등록 서버·도구와 목적지 ID의 실제 해석은 다음 Catalog 단계가 담당한다.

## 누적과 버전

알려진 READ 등급은 high-watermark를 올릴 수만 있다. 상승 시 `Escalation`을 추가하고
security_version을 1 올린다. 직전 READ 등급만 바뀌면 security_version은 유지한다.
row_revision과 Task 식별자·epoch·Profile·policy_version은 이 함수가 변경하지 않는다.

`observed=None`은 반환 자료의 **분류 실패**다. unclassified=True와 HALTED를 함께
설정하고 기존 최고 등급과 승격 근거를 보존한다. 이후 알려진 등급이 들어와도
미분류 플래그를 해제하거나 Task를 RUNNING으로 복귀시키지 않는다.

이미 unclassified=True·HALTED인 상태에 다시 None이 오면 원 상태를 반환한다.
6.1절 예제는 None마다 버전을 증가시키지만, 3.4절과 B2 체크리스트의
“보안 의미가 있는 값이 바뀔 때” 기준을 적용해 변화 없는 반복 관측은 멱등 처리했다.
원본 기술설계서는 수정하지 않았다.

자료가 없는 알려진 READ 전송 실패와 SEND 응답은 merge를 호출하지 않아야 한다.
이 구분과 원자적 저장·커밋 후 공개 집행은 이후 Guard·응답 게이트와 C Store의 책임이다.

기밀 조회 → 공개 조회 → 외부 전송의 순수 판정에서는 accumulate가 DENY를 유지하고,
last_read·none은 PUBLIC으로 판정할 수 있다. 세 모드 모두 등록·지문·미분류·Task 상태·
정책 버전·목적지·AGT 검사를 공유한다. 유효 등급 계산만 달라진다. 알 수 없는 모드는
`core.policy.PolicyConfigError`로 실패하며 설정 원문을 오류 메시지에 넣지 않는다.

## 검증 범위

2026-10-11 Python 3.12.15 가상환경에서 **전체 단위·성질 테스트 340개 통과**를
확인했다(B1 151개, B2 추가 189개). B2에는 경계 21조합, 서버·목적지·AGT 36조합,
phase별 모든 Task 상태, 9개 Hypothesis 성질 함수가 포함된다.
Ruff check·format check와 strict mypy도 통과했다. 외부 의존성 추가는 없다.

| 검증 | 설계 연결 |
| --- | --- |
| 유효 경계의 등급·tier·max_label 결정표, 서버·목적지·AGT 합성 | I2 정책 부분, T26·T27·T30·T31 판정 부분 |
| 등록·지문·phase·상태·정책·미분류·미지 목적지·OTHER 우선 거부 | I6, I10 판정 부분, T04 일부 |
| 고수위 단조·미분류 고정·버전·승격 근거·불변 입력 | I4, T05 |
| 세 모드의 유효 등급과 동일한 전제조건 | T34 순수 판정 부분 |
| Hypothesis의 등급·상한 단조성, DENY 흡수, 전제 우선, 결정성 | 기술설계서 5.4절 |
| Hypothesis의 관측 순서·최고 등급·미분류·버전 성질 | I4·I6 |
| AGT 원문 사유·잘못된 모드 원문이 반환값·예외 표현에 남지 않음 | I9 순수 반환값 부분 |

검사는 저장소의 Python 3.12 가상환경에서 실행한다.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/unit -q
& '.\.venv\Scripts\python.exe' -m ruff check .
& '.\.venv\Scripts\python.exe' -m ruff format --check .
& '.\.venv\Scripts\python.exe' -m mypy
```

단위·성질 검증은 실제 dispatch·wire·수신 부작용, SQLite 원자성, 사람 승인,
로그 유출 방지, 커밋 이후 공개를 증명하지 않는다. 해당 계수·통합·공격·결함 주입·
실험 검증과 정적 강제 규칙·CI는 C의 후속 작업이다.

## 다음 순서

1. B3 Catalog: 고정 도구·인자 스키마, 서버·목적지 해석, 출처 규칙과 등급 분류.
2. digest·snapshot·failclosed: 승인 사본과 예약 전후 오류 의미 준비.
3. B4 Guard·응답 게이트: Task 직렬화, 실행 예약, 격리·원자적 커밋·공개.
4. B5 승인: 사람 제어 채널, 재평가·사본 일치·원자적 소비 연결.
5. A·C 담당 리뷰와 실제 어댑터·Store·수신 계수의 연동 검증.

Catalog의 안전 경로 정규화에는 C의 위험 연산 구현이 필요하다. 실제 실행 경로를
연결하기 전에 A의 Transport·AccessChecker와 C의 Registry·Store를 확인하고,
B1 미확정 계약의 합의 기록을 남겨야 한다.
