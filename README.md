# jira-mcp

Atlassian Jira Cloud 용 MCP 서버. stdio 전송, 현재 **읽기 전용**.

> 코드를 손대기 전에 **[CLAUDE.md](CLAUDE.md)** 를 읽는다. 거기에만 적힌 함정이 있다.

## 도구

| 도구 | 하는 일 |
|---|---|
| `whoami` | 접속 계정 확인 |
| `search_issues(jql, limit=25, columns="")` | JQL 검색. 이슈를 찾는 단일 창구 |
| `read_issue(key)` | 이슈 하나를 본문·코멘트·첨부까지 |

### `columns` — 검색 결과에 열 덧붙이기

기본 열은 `key / 유형 / 상태 / 담당자 / 수정일 / 제목` 이다.
운영 필드를 표로 같이 보고 싶으면 쉼표로 나열한다.

```
기한, 생성, 우선순위, 보고자, 솔루션, Actual start, Actual end, 일정준수, 지연사유
```

```
search_issues(jql='project = PROJ AND cf[12473] = "제품A"',
              limit=100, columns="기한,Actual end,일정준수")
```

### JQL 에서 커스텀 필드

**한글 필드명은 동작하지 않는다.** 동명 필드가 여럿이라 Jira 가 해석하지 못하는데,
에러 대신 **0건을 돌려준다.** `cf[ID]` 로 쓴다.

```
project = PROJ AND cf[12473] = "제품A"      # 솔루션
project = PROJ AND cf[12594] != "On Time"      # 일정 준수 여부
```

자주 쓰는 이름(`솔루션`, `일정 준수 여부`, `지연 사유`, `고객사명(통합키)`)은
서버가 `cf[ID]` 로 자동 치환한다. 필드 ID 표는 [docs/JIRA-FIELDS.md](docs/JIRA-FIELDS.md).

## 설정

`.env` (git 제외됨):

```
JIRA_SITE=https://your-site.atlassian.net
JIRA_CLOUD_ID=<사이트의 cloud id>
JIRA_EMAIL=...
JIRA_API_TOKEN=...          # scoped 토큰, 범위: read:jira-work / read:jira-user
JIRA_AUTH_MODE=gateway      # auto | gateway | site
```

`JIRA_AUTH_MODE` 는 base URL 을 고른다. **scoped(범위 포함) 토큰은 gateway 로만**,
범위 없는 클래식 토큰은 site 로만 동작한다. `auto` 면 첫 호출 때 되는 쪽을 찾는다.

- gateway: `https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3`
- site: `https://your-site.atlassian.net/rest/api/3`

## 설치

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt     # Windows
.venv/bin/python -m pip install -r requirements.txt             # Linux / macOS
```

검증된 조합: **Python 3.14.3 / mcp 2.2.0 / httpx 0.28.1 / python-dotenv 1.2.3**.
`requirements.txt` 가 버전을 고정한다 — mcp 1.x 는 `FastMCP` 라 import 부터 깨진다.

## 검증

```bash
# 인증만
.venv\Scripts\python.exe check_auth.py          # Windows
.venv/bin/python check_auth.py                  # Linux / macOS

# MCP 규격대로 전체 (23개 체크)
.venv\Scripts\python.exe smoke_test.py
```

**Windows 에서 스크립트를 직접 돌릴 때는** 콘솔이 cp949 라 한글 출력이 깨진다.
`PYTHONIOENCODING=utf-8 PYTHONUTF8=1` 을 앞에 붙인다.

## 등록

절대경로로 넣는다. Claude Code 가 임의의 작업 디렉터리에서 실행한다.

```bash
# Windows
claude mcp add --scope user jira -- D:\dev1\jira-mcp\.venv\Scripts\python.exe D:\dev1\jira-mcp\run.py

# Linux / macOS
claude mcp add --scope user jira -- /경로/jira-mcp/.venv/bin/python /경로/jira-mcp/run.py
```

해제는 `claude mcp remove jira`.
**서버 코드를 고치면 Claude Code 를 재시작해야** 반영된다 (도구 목록은 기동 때 한 번만 읽는다).

## 알아둘 것

- **제약 없는 JQL 은 Jira 가 거부한다.** `order by ...` 만 주면 `updated >= -90d` 가 자동으로 씌워진다.
- **Jira 는 인식 못 하는 필드명에 에러가 아니라 0건을 돌려준다.** 0건을 단정의 근거로 쓰지 않는다.
- `status = 종료` 는 JQL 에서 안 먹는다. 화면 표시명과 JQL 상태명이 다르다.
- 구 검색 엔드포인트 `/rest/api/3/search` 는 **410 Gone**. `/search/jql` 만 쓴다.
  응답에 `total` 이 없고 `nextPageToken` / `isLast` 로 페이징한다 (`startAt` 아님).
- 본문·코멘트는 ADF(중첩 JSON)라 `jira_mcp/adf.py` 가 평문으로 누른다.
- stdio 에서 **stdout 은 프로토콜 전용 채널**이다. 로그를 stdout 에 쓰면 서버가 깨진다.
- mcp SDK 2.x 기준 (`MCPServer`, 구 `FastMCP` 에서 이름이 바뀜).

## 문서

| 문서 | 내용 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 손대기 전에 읽을 것. 함정·개발 루프·설계 원칙 |
| [docs/INTERNAL.md](docs/INTERNAL.md) | 조직 특정 문서를 어디에 두는지, 서버로 옮기는 법 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 구조·흐름·원리 |
| [docs/JIRA-FIELDS.md](docs/JIRA-FIELDS.md) | 커스텀 필드 ID, 찾는 법, JQL 함정 |
| [docs/WORKING-RULES.md](docs/WORKING-RULES.md) | 사용자 상시 요구사항 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 설치, 그리고 stdio → HTTP 이전 |

## 일일 취합 에이전트 (`agent/`)

솔루션 현황을 매일 1회 취합해 웹훅으로 보낸다. **MCP 를 쓰지 않고** `jira_mcp.client` 를
직접 import 하므로 클라우드 서버에 `mcp` 패키지가 필요 없다.

```bash
python -m agent.daily            # dry-run. 보낼 내용만 출력
python -m agent.daily --send     # 실제 발송
python -m agent.daily --no-llm   # 요약 없이 표만
```

- 판정은 고정 코드(`agent/collect.py`)가 한다 — 매일 같은 입력에 같은 결과가 나와야 하므로.
- LLM 은 요약 한 문단만 쓴다. **실패해도 리포트는 그대로 나간다.**
- 수집이 실패하면 발송하지 않고 exit 1 — "이상 없음" 과 "확인 못 함" 을 구분한다.
- 설치는 `requirements-agent.txt`, 배포는 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 5장.

## 다음 (미구현)

쓰기 도구 — `create_issue` / `transition_issue` / `add_comment`.
`JIRA_ALLOW_WRITE` 스위치 + 프로젝트 화이트리스트를 앞에 두기로 함.
토큰 범위에 `write:jira-work` 추가 필요.
붙이기 전에 프롬프트 인젝션 대비를 읽는다 — [CLAUDE.md](CLAUDE.md) 6장.

## 웹 화면

에이전트 결과를 브라우저로 보고 미리보기·발송을 누르는 화면이 `web/` 에 있다. → `docs/WEB.md`
