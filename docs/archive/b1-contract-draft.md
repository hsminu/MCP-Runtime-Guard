# B1 도메인·포트 계약 구현 초안

기준은 `technical-design.md`다. 작성일은 2026-10-11이고 작업 브랜치는 `feat/b-security-core`다.
사용자의 구현 착수 지시에 따라 B1의 검토 가능한 코드와 단위 테스트를 작성했다.
**A·C의 합의·동결 기록은 아직 없다.** 이 문서의 추가 필드·시그니처는 검토용 제안이다.

## 구현 범위

| 파일 | 구현한 내용 | 설계 근거 |
| --- | --- | --- |
| `src/guard_app/domain/enums.py` | 등급·판정·신뢰·도구·등록·Task·승인·요청 상태 | 3.2·8.2·11.2절 |
| `src/guard_app/domain/reasons.py` | 판정과 승인·결과 처리 reason code | 5.5절 |
| `src/guard_app/domain/models.py` | 3.3절 모델 10종, 엄격 검증, 검증된 사본 생성 | 3.1–3.4절 |
| `src/guard_app/domain/json_types.py` | 중첩 JSON의 독립 사본과 불변 보관 | 3.1·8.1절 |
| `src/guard_app/domain/errors.py` | 해석·저장·dispatch 오류의 고정 사유 | 7.1·9.2절 |
| `src/guard_app/domain/contracts.py` | 원시·공개 결과, Guard 결과 5종, 승인·검사·등록·모델 DTO | 0.5·6–12장 |
| `src/guard_app/ports.py` | Transport·AccessChecker·Scanner·ModelClient·Registry·Store Protocol | 0.5·7–12장 |
| `tests/unit/` | 모델·결과·오류의 단위 및 JSON 성질 검증 | I3·I6·I7·I9의 단위 부분 |

정책 엔진, Catalog, Guard, 승인 서비스, 응답 게이트는 다음 단계다.
A의 SDK·GUI·Runner와 C의 SQLite·Registry·Scanner·위험 연산·독립 통합 테스트는 구현하지 않았다.
Protocol에는 행위와 오류 의미를 명시했으며 실제 I/O 구현은 없다.

## 모델과 신뢰 입력

- 모델 제안은 `ProposedCall`로 검증한다. Task·epoch·등급·경계·승인자 필드는 없다.
- 신뢰 설정과 실행 상태로 만든 `ResolvedRequest`는 별도 타입이다.
- `Frozen`은 `frozen=True`, `strict=True`, `extra="forbid"`를 사용한다.
- Python 입력에서 enum은 해당 enum 인스턴스가 필요하다. JSON의 Label·Decision은 정확한 정수만 허용하며 bool·float·문자열을 거부한다.
- 카운터·버전은 음수를 거부한다. fingerprint와 승인 해시는 소문자 SHA-256 64자리다.
- 외부 경계의 `max_label`은 INTERNAL 이하만 허용한다(5.1절).
- `PolicyInput`은 요청과 상태의 Task·epoch가 같은지 확인한다.
- `SEND.destination=None`, 미등록 상태, 지문 불일치의 최종 DENY 집행은 이후 Catalog·정책 구현의 책임이다. 모델 생성 성공은 허용 판정이 아니다.

## 중첩 인자의 불변성

`Arguments`와 `FrozenJsonObject`는 입력 dict·list를 재귀적으로 복사한다.
객체는 읽기 전용 mapping, 배열은 tuple로 보관한다. `to_dict()`와 모델 직렬화는
매번 독립된 dict·list를 반환하며, 모델이 보관한 값을 외부에 가변 형태로 노출하지 않는다.

- Arguments는 8.1절의 str·int·bool·list·dict·None만 허용한다. float·NaN·비 JSON 값·순환 입력을 거부한다.
- 문자열은 UTF-8로 표현 가능해야 하며 NFC/NFD 변환은 하지 않는다.
- 중첩 깊이는 루트 0 기준 64까지 허용하는 초안 선택이다. 크기·바이트 상한은 이후 Catalog·응답 게이트에서 별도로 정한다.
- schema·raw payload용 `FrozenJsonObject`는 유한 float를 허용한다. 스키마 지문의 정규화 규칙은 C와 확인해야 한다.
- `model_copy(update=...)`는 전체 모델을 다시 검증한다. deep 값과 관계없이 중첩 컨테이너를 분리한 사본을 만든다.
- 표준 `copy.deepcopy`의 지원은 약속하지 않는다. 복사는 검증된 `model_copy` 또는 독립 JSON 사본을 사용한다.
- `model_construct`, `object.__setattr__`, BaseModel의 검증 우회 API는 비신뢰 입력에 사용하지 않는다. 이 타입은 로컬 Python 코드를 격리하는 샌드박스가 아니다.

## 원문 공개와 오류

인자·스키마·원시 결과·공개 본문·모델 대화는 기본 repr에서 제외했다.
`hide_input_in_errors=True`는 ValidationError의 문자열에서 입력 값을 숨긴다.
**`ValidationError.errors()`·`model_dump()`에는 원문이 들어갈 수 있으므로 로그나 UI 오류에 그대로 전달하지 않는다.**
필드 이름까지 포함한 예외 문자열을 안전한 감사 메타데이터로 간주하지 않는다.
실제 fail-closed·로그 경로에서는 예외 타입과 고정 reason code만 사용할 예정이다.

`RawResult`는 격리된 SDK 결과의 JSON 사본이다. `failed=True`는 자료가 전혀 없는
알려진 전송 실패만 뜻한다. 오류 본문에 자료가 섞인 결과는 payload가 있는 수신 결과로 분류한다.
`ReleasedResult`를 생성하는 행위 자체는 커밋이나 공개 허가의 증명이 아니다.
실제 서비스는 응답 게이트에서 검사와 커밋을 완료한 후에만 생성·공개해야 한다.
`Released.label=None`은 SEND 결과용이며 READ 미분류와 구분한다.

## A·C와 확인할 선택

| 대상 | 이번 초안 | 필요한 확인 |
| --- | --- | --- |
| 결과 모델 | kind discriminator와 reasons, 격리 JSON payload, 공개 text·structured_content | A의 SDK 결과 변환과 Runner 처리 |
| TrustedContext | TaskState와 사전 등록 operator_id | A의 AGT 번역에 필요한 최소 정보 |
| Scanner·ModelClient | 불변 tuple 입력과 provider 중립 대화 DTO | A·C의 라이브러리 형식·오류 번역 |
| 승인 레코드 | UTC-aware datetime, 생성·결정·소비 시간 검증 | C의 UTC ISO 문자열 저장·복원 |
| Store 커밋 | 호출자가 UTC 시각 주입, READ 커밋은 TaskState 반환 | C의 writer·트랜잭션·상태 충돌 처리 |
| 승인 생성 | 승인·요청 WAITING·감사 원자 기록 | Task WAITING 전이와 snapshot 계산 시점까지 함께 정해야 함 |
| 승인 종결 | 전용 transition_approval | 거절·만료·무효화와 Task 복귀를 원자로 묶는 방식 |
| 예약 후 실패 | commit_request_failure의 요청·Task·감사 원자 기록 | 실패 유형별 메서드 분리 및 global halt 연결 |
| 상태 버전 | security_version과 row_revision 분리 | WAITING↔RUNNING 전이의 보안 버전 의미(3.4·8.3절) |
| UI 명령·이벤트 | 상세 payload 미정 | A와 실행·제어 큐, 승인 주체, 상태·reason 표시 확정 |

특히 `commit_observation`, `commit_send_outcome`, `consume_and_reserve`의 원자성은
포트에 적힌 요구사항이다. 이 초안의 단위 테스트로 실제 SQLite 원자성을 증명하지 않는다.

## 환경과 검사

초기 B1 환경은 Python 3.12.15, Pydantic 2.14.0에서 확인했다.
현재 `pyproject.toml`과 `requirements.lock`에는 [B3 Catalog](b3-catalog.md)의 JSON Schema
검증 의존성도 추가되어 있다. MCP SDK·AGT·Scanner·PySide6의
전체 연동 조합은 A·C의 실제 호환 검증 후 고정한다(부록 A).

새 Python 3.12 가상환경에서 저장소 루트를 기준으로 설치한다.

```powershell
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pytest tests/unit -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pip check
```

이 PC의 Windows 애플리케이션 정책이 mypy의 native parser DLL 로딩을 거부했다.
설정의 `native_parser=false`로 같은 strict 타입 검사를 Python parser에서 실행한다.
보안·타입 검사를 끄거나 Windows 정책을 바꾸지 않았다.
Ruff 대상은 Python·pyproject 파일로 한정해 원본 기술설계서의 코드 블록을 보존한다.
pre-commit·Semgrep·import-linter 강제 규칙·CI는 C의 후속 구현 대상이다.

## 검증과 다음 순서

B1 단위 테스트는 151개다. 허용 JSON의 freeze·직렬화·사본 성질은 Hypothesis로 검증한다.
정적 타입과 단위 검증은 모델 계약의 일부를 확인하며 I1–I10 전체 집행의 완료를 뜻하지 않는다.
실제 전송, DB 소비, 사람 전용 제어 큐, 커밋 이후 공개는 이후 C의 계수·통합·공격·결함 검증이 필요하다.

1. A·C와 모델·포트·상태 버전·미정 이벤트를 검토하고 합의 기록을 남긴다.
2. B2 정책 행렬·판정 합성·high-watermark·Unclassified 성질 구현은 [B2 구현·검증 문서](b2-policy-and-taint.md)에 기록한다.
3. B3 Catalog 독립 구현과 남은 C 경로 연결은 [B3 문서](b3-catalog.md)에 기록한다.
4. digest·snapshot·failclosed를 준비하고 B4 Guard·응답 게이트를 구현한다.
5. B5 승인 재평가·사본 일치·원자적 소비를 연결한다.
6. A·C 실제 구현을 리뷰하고 통합·공격·결함 주입 검증을 완료한다.
