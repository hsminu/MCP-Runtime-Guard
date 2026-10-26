# A108 · 실제 ChatGPT 로그인과 실제 모델로 Guard 도구 호출 turn

2026-10-27. A107과 같은 제품 경로를 실제 모델로 돌렸다. 실제 모델이 Guard 도구를 제대로 고르는지, 그리고 실제 로그인 환경에서 무엇이 깨지는지를 봤다. [근거](../evidence/a108-codex-real-login-2026-10-27.json)

## 방법

- 실제 구성요소: codex.exe 0.162.1, 제품 L1 설정 그대로(가짜 모델 플래그 없음), C2.12 launcher, C2.13 앱 폴더, SessionRuntime부터 Guard·SQLite까지
- 모델: ChatGPT 로그인으로 쓰는 실제 gpt-6.1-sol. 모델 정보는 고정한 `model_catalog.json`이다. 플랜 사용량에서 차감되고 토큰당 과금은 없다.
- 대역: MCP 서버(메모리 Peer, 합성 문구), Registry, Profile 문서
- 스파이크에만 둔 우회 두 개
  - L2 통과 영수증을 직접 넣었다(A107과 같음).
  - CODEX_HOME 허용 목록에 `secrets`를 넣었다(발견 1). 스파이크 프로세스 안에서만 바꿨다.

## 결과: 실제 모델

| 시나리오 | 결과 |
| --- | --- |
| "public/a.txt를 읽고 요약해" | 모델이 `docs.read({"path": "public/a.txt"})`를 직접 불렀다. ALLOW, MCP 1회, 답변으로 끝남. Task RUNNING·PUBLIC |
| "internal/a.txt를 읽고 partner에게 한 줄 요약을 보내" | 모델이 read 뒤 `docs.send({"to": "partner", ...})`를 불렀다. ASK에서 멈췄다(승인 전 MCP 호출은 읽기 1회뿐). 승인하자 전송 1회가 나가고 같은 turn이 끝났다. Task INTERNAL |

- 두 경우 모두 callId마다 응답 1회, interrupt 0, L3 정지 없음, `request_user_input` 0이다.
- 고정한 모델 정보로 실제 ChatGPT 로그인에서도 Guard 도구를 쓸 수 있었다. C-CODEX-03 조치가 이번 두 번의 실행에서는 버텼다. 두 번이라 보장은 아니다.
- Codex가 덧붙이는 permissions 안내(A107 발견)는 이번 두 번에서 문제를 일으키지 않았다.

## 발견 1: 로그인하면 제품이 세션을 시작할 수 없다

- `cli_auth_credentials_store = "keyring"`이어도 Codex 0.162.1은 인증 정보를 `CODEX_HOME/secrets/codex_auth.age`에 age로 암호화해 저장한다. 암호(passphrase)만 Windows 자격증명 관리자에 둔다(`codex-rs/secrets/src/local.rs`).
- C6.19의 CODEX_HOME 허용 목록은 `installation_id`·`.sandbox_migration`·`tmp`만 받는다. 그래서 로그인한 다음 앱이 시작될 때 `write_codex_home`이 `CODEX_HOME_UNEXPECTED_ENTRY`를 낸다. 세션 준비가 `CODEX_PREPARATION_FAILED`가 되고 모든 세션이 거부된다. 실제로 재현했다.
- 이 문제는 로그아웃으로도 풀리지 않는다(발견 3).
- C의 T43 질문("CODEX_HOME에 인증 파일을 따로 쓰는지")의 답이기도 하다. 쓴다. 다만 암호화된 파일이다.
- 처리 방법(허용 목록에 `secrets/`와 정해진 파일 이름을 넣을지, 다른 저장 방식을 쓸지)은 C가 정할 일이다. CODEX_HOME 해시 대조 범위와 I9(원문 비잔존)도 함께 봐야 한다.

## 발견 2: 기기 코드 로그인이 막힌 계정이 있다

- 기기 코드 로그인(`chatgptDeviceCode`, A89)은 "ChatGPT 보안 설정에서 Codex 등의 기기 코드 로그인을 활성화하라"는 화면에서 멈췄다.
- 설정을 켠 뒤 새 코드로 다시 해도 같은 화면이었다. 원인은 확정하지 못했다.
- 같은 app-server의 브라우저 로그인(`account/login/start {type: "chatgpt"}`)은 성공했다. `authUrl`은 `127.0.0.1:1455`로 돌아오고, `originator=mcp_runtime_guard`가 붙는다.
- A89 로그인 화면은 기기 코드와 API 키만 지원한다. 이런 계정은 제품에서 ChatGPT로 로그인할 수 없다. 화면에서 안내만 할지, 브라우저 로그인을 지원할지는 사용자 결정이다.

## 발견 3: 로그아웃이 남기는 것

- `account/logout` 뒤 계정 상태는 `SIGNED_OUT`이다.
- 그러나 `secrets/codex_auth.age`(208바이트)와 자격증명 관리자 항목 `secrets|<경로 해시>.codex`가 남는다. 발견 1 때문에 로그아웃한 뒤에도 앱이 세션을 시작할 수 없다.

## 남은 한계

- 실제 모델 실행은 시나리오마다 1번씩이다. 모델이 매번 같은 도구를 고른다는 보장은 아니다.
- MCP 서버·Registry·Profile은 대역이다. L2는 우회했다.
- 기기 코드 실패 원인은 확정하지 못했다.
- 스파이크가 남긴 자격증명 관리자 항목 1개와 스파이크 폴더는 사용자 확인 뒤 지운다.

관련: I1·I3·I9·I11, T43(C), C-CODEX-03·C-CODEX-05. PR은 아직 없다(A 브랜치 작업).
