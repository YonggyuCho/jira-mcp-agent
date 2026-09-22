# jira-mcp

Atlassian Jira Cloud (`your-site`) 용 MCP 서버. stdio 전송, 현재 **읽기 전용**.

## 도구

| 도구 | 하는 일 |
|---|---|
| `whoami` | 접속 계정 확인 |
| `search_issues(jql, limit=25)` | JQL 검색. 이슈를 찾는 단일 창구 |
| `read_issue(key)` | 이슈 하나를 본문·코멘트·첨부까지 |

## 설정

`.env` (git 제외됨):

```
JIRA_SITE=https://your-site.atlassian.net
JIRA_CLOUD_ID=<cloud id>
JIRA_EMAIL=...
JIRA_API_TOKEN=...          # scoped 토큰, 범위: read:jira-work / read:jira-user
JIRA_AUTH_MODE=gateway      # auto | gateway | site
```

`JIRA_AUTH_MODE` 는 base URL 을 고른다. **scoped(범위 포함) 토큰은 gateway 로만**,
범위 없는 클래식 토큰은 site 로만 동작한다. `auto` 면 첫 호출 때 되는 쪽을 찾는다.

- gateway: `https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3`
- site: `https://your-site.atlassian.net/rest/api/3`

## 검증

```
.venv\Scripts\python.exe check_auth.py
```

## 등록

```
claude mcp add --scope user jira -- D:\dev1\jira-mcp\.venv\Scripts\python.exe D:\dev1\jira-mcp\run.py
```

해제는 `claude mcp remove jira`.

## 알아둘 것

- **제약 없는 JQL 은 Jira 가 거부한다.** `order by ...` 만 주면 `updated >= -90d` 가 자동으로 씌워진다.
- 구 검색 엔드포인트 `/rest/api/3/search` 는 **410 Gone**. `/search/jql` 만 쓴다.
  응답에 `total` 이 없고 `nextPageToken` / `isLast` 로 페이징한다.
- 본문·코멘트는 ADF(중첩 JSON)라 `jira_mcp/adf.py` 가 평문으로 누른다.
- stdio 에서 **stdout 은 프로토콜 전용 채널**이다. 로그를 stdout 에 쓰면 서버가 깨진다.
- mcp SDK 2.x 기준 (`MCPServer`, 구 `FastMCP` 에서 이름이 바뀜).

## 다음 (미구현)

쓰기 도구 — `create_issue` / `transition_issue` / `add_comment`.
`JIRA_ALLOW_WRITE` 스위치 + 프로젝트 화이트리스트를 앞에 두기로 함.
토큰 범위에 `write:jira-work` 추가 필요.
