# C · 실제 MCP 도구 정의 조사

2026-10-13 사용자 요청으로 공개 MCP 서버 6종·설정 7개에 실제 연결해 `initialize`와 `tools/list`를 수행했다. **이번 표본에서는 도구 하나의 전체 정의에 64KiB를 적용해도 입력 누락이 없었다. 서버 전체 목록에는 별도 상한이 필요하다.** 가장 큰 도구 정의는 8,360바이트이고 GitHub 전체 도구 목록의 한 페이지는 259,485바이트였다.

[측정 근거 JSON](../evidence/real-mcp-metadata-2026-10-13.json)에 도구별 크기·해시·필드 존재·프로토콜 계수·버전과 배포물 출처를 기록했다. 조사 당시 64KiB를 초기 기본값으로 제안했다. 후속 "너 추천대로 해 다음 설명" 지시로 [도구당 기본 64KiB·설정 가능 정책](../contracts.md#scanner-입력-크기-사용자-결정)을 확정했으며 제품 기본값·경고 연결은 미반영이다.

## 실제 수집 결과

크기는 **전체 Tool 객체를 키 정렬·공백 없는 UTF-8 JSON으로 직렬화한 바이트 수**다. 도구 정의 합계는 각각의 객체 크기를 더한 값이다. 페이지 크기는 `tools/list`의 JSON-RPC `result`를 같은 방식으로 재직렬화한 값이며 네트워크에서 전송된 원래 본문 길이는 아니다. KiB는 1,024바이트다.

| 서버 / 설정 | 고정한 배포 버전 | 도구 수 | 최대 단일 정의 | 정의 합계 | 최대 페이지 result |
| --- | --- | ---: | ---: | ---: | ---: |
| Filesystem | npm 1.0.0 | 14 | 1,272 B | 13,098 B | 13,123 B |
| Memory | npm 1.0.0 | 9 | 1,639 B | 11,055 B | 11,075 B |
| Sequential Thinking | npm 1.0.0 | 1 | 4,640 B | 4,640 B | 4,652 B |
| Playwright | npm 0.0.83 | 25 | 1,629 B | 20,260 B | 20,296 B |
| GitHub 기본 toolsets | release v2.0.2 | 46 | 5,768 B | 126,553 B | 126,642 B |
| GitHub `--toolsets all` | release v2.0.2 | 91 | **8,360 B** | **259,351 B** | **259,485 B** |
| DeepWiki 원격 | 서버 응답 2.14.3 | 3 | 655 B | 1,534 B | 1,548 B |

- 수집한 도구 항목은 189건이다. GitHub 기본 46개의 이름이 all 설정에도 있어 독립 도구 189개로 해석하지 않는다. 서버 종류와 도구 이름의 조합은 143개다.
- 두 GitHub 설정에서 `get_label`은 `icons`만 달랐다. 따라서 정의 해시까지 구분한 조합은 144개다. 차이의 원인은 확인하지 않았으며 공격이나 결함으로 판정하지 않았다.
- **64KiB 초과 0건**, 현재 `prepare_tool_definition(..., max_definition_bytes=65536)`에서 **전체 정의 보존 189/189건**을 확인했다. 준비된 JSON을 다시 파싱해 원래 Tool 객체와 동일한지 비교했다.
- 모든 설정은 한 페이지였고 협상한 프로토콜은 **2025-11-25**였다. MCP Python SDK는 1.30.0, Python은 3.12.3이다.
- Playwright의 npm 배포 버전은 0.0.83이며 실제 `serverInfo.version`은 `1.64.0-alpha-1790635538000`이다. 패키지 버전과 서버 자기 보고 버전을 구분했다.

Filesystem·Memory·Sequential Thinking은 [MCP 공식 reference 서버 저장소](https://github.com/modelcontextprotocol/servers)의 배포 패키지를 사용했다. Playwright는 [Microsoft 공식 서버](https://github.com/microsoft/playwright-mcp), GitHub는 [공식 v2.0.2 배포물](https://github.com/github/github-mcp-server/releases/tag/v2.0.2)을 사용했다. DeepWiki는 공식 문서의 공개 Streamable HTTP 주소에 접속했다. [DeepWiki 연결 문서](https://docs.devin.ai/work-with-devin/deepwiki-mcp).

## 실제 정의에서 확인한 입력 범위

기본 네 필드 외의 데이터가 실제로 있었다. 아래 필드는 수집한 Tool 객체의 최상위 필드이며 서버 초기화 정보나 도구 실행 결과와 구분한다.

| 서버 | 네 기본 필드 외에 관찰한 필드 |
| --- | --- |
| Filesystem·Memory·Sequential Thinking | `title`, `outputSchema`, `execution` |
| Playwright | 추가 최상위 필드 없음 |
| GitHub | 모든 도구에 `icons`, 각 설정의 5개 도구에 `_meta` |
| DeepWiki | 모든 도구에 `outputSchema`, `_meta` |

MCP 규격도 `title`, `icons`, `outputSchema`, `execution`을 Tool 정의에 포함하며 `tools/list` 페이지네이션을 정의한다. annotations는 신뢰하는 서버에서 온 경우를 제외하고 신뢰하지 않아야 한다. 확인한 Tools 규격에는 Tool 전체에 대한 64KiB 상한이 없다. 64KiB는 우리 Scanner 입력 정책으로 정하는 값이다. [협상한 2025-11-25 Tools 규격](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), [2026-07-28 Tools 규격](https://modelcontextprotocol.io/specification/2026-07-28/server/tools). 후자의 문서를 읽은 것과 실제 호환성을 검증한 것은 구분한다.

가장 큰 GitHub `projects_write`는 설명문 171바이트, `inputSchema` 7,035바이트, 전체 정의 8,360바이트였다. 크기를 설명문만으로 판단하면 실제 입력량을 놓친다. 위 개별 값은 각 부분을 별도로 측정한 값이므로 JSON 키·구분자까지 포함한 전체 크기와 단순 합산하지 않는다.

`get_label`의 아이콘만 달라진 관찰은 현재 설계 원문의 네 필드 지문과 전체 검사 입력 사이에 차이가 있음을 보여 준다. 추가 필드 변경을 I10 지문에 어떻게 포함할지는 **B digest·공통 계약 검토 항목**이다. 이번 조사에서 B 구현을 변경하거나 새 지문 계약을 확정하지 않았다.

초기화 응답에는 별도 `instructions`도 있었다. GitHub 기본 1,802바이트, all 6,763바이트, DeepWiki 3,494바이트였다. 현재 Scanner 입력은 Tool 정의이므로 이 서버 지시문은 이번 입력 보존 검증 대상이 아니다. 검사 범위를 넓히려면 별도 결정을 기록해야 한다.

DeepWiki 공식 문서는 질의 도구를 `ask_question`으로 소개하지만 실제 목록의 이름은 `ask_wiki_question`이었다. 실제 목록의 나머지 두 이름은 `read_wiki_structure`, `read_wiki_contents`다. 도구 연결은 문서에서 이름을 고정하기보다 해당 세션의 `tools/list`를 근거로 해야 한다. 문서와 응답의 차이를 기록한 것이며 도구 실행은 하지 않았다.

## 입력 크기 정책에 대한 제안

1. **도구 하나의 전체 정의는 기본 64KiB, 설정 가능**을 초기안으로 권장했고 후속 사용자 결정으로 확정했다. 관찰한 최대 8,360바이트보다 약 7.8배 크고 189건 모두 잘리지 않았다. 작은 목적 표본이므로 모든 MCP 정의가 이 크기 안에 든다고 보장하지 않는다. 정책 확정과 제품 기본값 구현은 구분한다.
2. **페이지 응답 크기·서버 전체 누적 크기·도구 수를 별도로 제한**한다. GitHub all의 한 페이지는 약 253.4KiB이고 기본 설정도 약 123.7KiB다. 도구별 64KiB를 목록 전체에 적용하면 이번에 수집한 정상 배포 설정도 수집할 수 없다. 후속 "ㅇㅇ 그렇게 해 다음 정할거" 결정으로 [페이지당 기본 1MiB·설정 가능 정책](../contracts.md#mcp-목록-수신-크기-사용자-결정)을, "그렇게 하자" 결정으로 [서버별 조회 누적 기본 8MiB·설정 가능 정책](../contracts.md#mcp-목록-누적-수신량-사용자-결정)을 확정했다. 조사한 정규화 result 크기와 수신 시 원래 응답 메시지 크기는 구분한다. 제품 수신 제한은 미구현이며 도구 수 등 나머지 제한은 미확정이다.
3. **초과 입력을 자르거나 일부 필드를 빼서 검사 완료로 표시하지 않는다.** 사용자 결정대로 검사 미완료와 고정 오류 코드를 경고로 남기고 사용은 계속하는 정책으로 연결한다. Guard 판정·승인·저장소 오류 처리·정의 변경 무효화 규칙은 별도다. 크기 초과의 제품 경고·Registry 연결은 아직 미구현이다.
4. **C 직렬화 상한과 A 수신 상한을 구분**한다. 현재 C 크기 검사는 파싱된 객체를 직렬화한 뒤 실행한다. 따라서 악성 서버의 거대한 본문이 파싱되기 전에 메모리 사용을 제한하는 기능은 아니다. 수신 중의 본문 제한과 중단 처리는 A transport 계약에서 정해야 한다.

복잡한 중첩 schema, 큰 enum, `outputSchema`, data URI 아이콘 등은 크기를 키울 수 있다. 이번 표본에서는 단일 정의가 64KiB를 넘는 사례를 관찰하지 않았다. 토큰 수는 바이트 수와 다르며 Scanner의 시스템 프롬프트·응답 토큰도 별도로 계산된다. GitHub all을 도구마다 한 번씩 LLM에 검사하면 입력 대상이 91개가 되므로 도구 수 상한·동시성·검사 시간 예산도 함께 고려해야 한다. 이번 조사에서 토큰 비용이나 지연을 측정하지 않았다.

## 실행 방법과 출처 고정

프로젝트 밖 임시 디렉터리에 아래 공개 배포 패키지만 설치했다. Node 20.20.2, npm 10.8.2이며 npm lifecycle script는 실행하지 않았다. 프로젝트 의존성이나 `.env`를 변경하지 않았다.

```bash
npm install --ignore-scripts --no-audit --no-fund \
  @modelcontextprotocol/server-filesystem@1.0.0 \
  @modelcontextprotocol/server-memory@1.0.0 \
  @modelcontextprotocol/server-sequential-thinking@1.0.0 \
  @playwright/mcp@0.0.83
```

해결된 TypeScript MCP SDK는 1.32.1이었다. 직접 사용한 패키지의 배포 URL·integrity와 실제 npm lock의 SHA-256을 근거 JSON에 남겼다. GitHub Linux x86_64 archive는 공식 release asset digest와 SHA-256을 대조한 뒤 실행 파일을 추출했다. 버전별 결과이며 이후 배포판이나 계정별 도구 수가 같다는 뜻은 아니다.

| 대상 | 실제 시작 구성 |
| --- | --- |
| Filesystem | `node node_modules/@modelcontextprotocol/server-filesystem/dist/index.js <빈 임시 허용 폴더>` |
| Memory | `node node_modules/@modelcontextprotocol/server-memory/dist/index.js`, `MEMORY_FILE_PATH`는 임시 JSONL 경로 |
| Sequential Thinking | `node node_modules/@modelcontextprotocol/server-sequential-thinking/dist/index.js`, `DISABLE_THOUGHT_LOGGING=true` |
| Playwright | `node node_modules/@playwright/mcp/cli.js --isolated --headless` |
| GitHub | 검증한 binary의 `stdio`, 별도 세션에서 `stdio --toolsets all` |
| DeepWiki | `https://mcp.deepwiki.com/mcp`, Streamable HTTP, 인증정보 없음 |

GitHub는 metadata 수집을 위해 실제 자격증명 대신 synthetic placeholder를 자식 프로세스 환경에 넣었다. 실제 계정 권한·인증 성공·GitHub 업무 API 호출은 검증하지 않았다. Playwright 브라우저 조작, 파일 읽기·쓰기, Memory 도구 실행도 하지 않았다. 원문 정의와 stderr는 임시 파일에만 보관했고 저장소에는 도구 이름·크기·해시·필드 존재와 버전 근거만 남겼다.

설치된 Python SDK의 stdio·Streamable HTTP client로 세션을 만들고, 송신 stream에서 프로토콜 메서드를 계수했다. `initialize`, `tools/list`, 초기화/취소 notification, `ping` 외의 송신 시도는 전송 전에 거부하도록 구성했다. 실제로는 설정마다 `initialize` 1회·`notifications/initialized` 1회·`tools/list` 1회만 관찰했다. 수신 stream의 JSON-RPC result를 **SDK Tool 객체로 정규화하기 전에** 수집하고 전체 필드를 직렬화했다. `nextCursor` 반복 처리는 준비했지만 이번 응답은 모두 단일 페이지라 다중 페이지 처리를 검증한 것은 아니다.

## 검증 범위와 남은 작업

| 확인 사항 | 이번 결과 | 연결하는 설계 항목 |
| --- | --- | --- |
| 공개 서버의 실제 stdio·원격 HTTP metadata 왕복 | 설정 7개 모두 수집 완료 | C1.4·C5, T17 입력 수집의 일부 |
| 현재 C 입력 어댑터의 전체 정의 보존 | 189/189 일치 | I10·T17 검사 대상 일치의 일부 |
| MCP `tools/call` 송신 | 0회, 허용되지 않은 메서드 시도 0회 | I1의 조사 범위 준수, Guard 통합 집행 증명은 아님 |
| 인증정보·전체 Tool 원문의 저장소 기록 | 실제 인증정보 사용 없음, 집계·해시만 저장 | I9·T19·S04의 조사 산출물 범위 |
| 수집한 실제 정의의 Luna low·YARA 검사 | **미실행**, 추가 모델 API 요청 0건 | T17·검사 실패 집계 후속 검증 |
| A 제품 transport·Registry·B ScanReport·Guard 연결 | **미실행**, B ScanReport 제외 유지 | T10·T17·C6 후속 인수 |
| 서버 내부 시작 시의 모든 네트워크·OS 부작용 | **관찰하지 못함** | MCP stream 계수를 패킷 캡처나 receiver 부작용 계수로 확대 해석하지 않음 |

이번 작업은 크기·필드 구성을 조사한 결과다. 해당 서버가 안전하다는 판정, 공격 탐지율, 실제 도구 실행 성공이나 Guard 차단 검증 결과가 아니다. 새 제품 소스·단위 테스트를 수정하지 않아 이전 62개 테스트와 정적 검사를 다시 실행하지 않았다. 조사 JSON의 집계·해시와 보고 표의 일치를 별도로 검증했다. 다음 단계는 나머지 목록 수신 제한을 정하고, 확정한 도구별 입력 정책·LLM/YARA 병행 실행·미완료 경고를 구현한 뒤 실제 정의를 검사하는 것이다.
