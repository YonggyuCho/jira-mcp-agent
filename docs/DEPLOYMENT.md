# 배포와 이전

지금은 **개발자 PC 에서 stdio 로** 돈다. 이 문서는 (1) 다른 기계에 그대로 세우는 법과
(2) 원격 서버로 옮길 때 무엇이 달라지는지를 적는다.

최종 확인: 2026-09-22

---

## 1. 검증된 실행 환경

| | 버전 |
|---|---|
| Python | 3.14.3 |
| mcp | 2.2.0 |
| httpx | 0.28.1 |
| python-dotenv | 1.2.3 |
| pydantic | 2.13.5 (mcp 의존) |
| anyio | 4.15.1 (mcp 의존) |

`requirements.txt` 는 직접 의존 3개를 **정확한 버전으로 고정**했다.
mcp SDK 2.x 는 1.x 에서 `FastMCP` → `MCPServer` 로 이름이 바뀌었으므로
버전을 풀면 import 부터 깨진다.

---

## 2. 같은 방식(stdio)으로 다른 기계에 세우기

```bash
git clone <저장소> jira-mcp
cd jira-mcp

python -m venv .venv

# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
# Linux / macOS
.venv/bin/python -m pip install -r requirements.txt

cp .env.example .env        # Windows: copy .env.example .env
# .env 에 JIRA_API_TOKEN 입력
```

### 인증 확인

```bash
# Windows
.venv\Scripts\python.exe check_auth.py
# Linux / macOS
.venv/bin/python check_auth.py
```

### Claude Code 에 등록

```bash
# Windows
claude mcp add --scope user jira -- C:\경로\jira-mcp\.venv\Scripts\python.exe C:\경로\jira-mcp\run.py
# Linux / macOS
claude mcp add --scope user jira -- /경로/jira-mcp/.venv/bin/python /경로/jira-mcp/run.py
```

경로는 **절대경로**여야 한다. Claude Code 가 임의의 작업 디렉터리에서 실행한다.

### 전체 검증

```bash
.venv/bin/python smoke_test.py      # Windows 는 .venv\Scripts\python.exe
```

23개 체크가 전부 PASS 여야 한다.

### Linux 로 갈 때 달라지는 것

| | Windows | Linux |
|---|---|---|
| 인터프리터 경로 | `.venv\Scripts\python.exe` | `.venv/bin/python` |
| 콘솔 인코딩 | `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` 필요 (cp949) | 불필요 |
| 줄바꿈 | CRLF 섞일 수 있음 | LF |

`smoke_test.py` 는 두 경로를 모두 찾도록 되어 있다.

---

## 3. 원격 서버로 옮길 때 — stdio → HTTP

> **이 장은 "MCP 서버 자체를 원격에 두고 내 PC 의 Claude Code 가 거기 접속하는" 시나리오다.**
> **매일 자동 취합이 목표라면 이 장이 아니라 5장을 본다.** 그 경우 MCP 가 아예 필요 없다 —
> 에이전트가 `jira_mcp.client` 를 직접 import 하므로 전송 방식도 인증 설계도 생기지 않는다.
> (그리고 MCP 만 서버에 둬도 그걸 부르는 Claude Code 가 PC 에서 돌아야 해서 목적을 못 이룬다.)

**여기서부터는 설계가 바뀐다.** 경로만 고치는 작업이 아니다.

### 무엇이 달라지는가

| | stdio (현재) | HTTP (원격) |
|---|---|---|
| 실행 형태 | Claude Code 가 자식 프로세스로 띄움 | 서버가 상시 대기 |
| 수명 관리 | 클라이언트가 켜고 끔 | systemd/컨테이너가 관리 |
| 인증 | `.env` 를 그냥 읽음 (PC 주인 = 사용자) | **접속자 인증이 새로 필요** |
| 사용자 구분 | 없음 (1인 1프로세스) | **여러 사용자가 한 서버에 붙음** |
| 노출 | 없음 | 네트워크에 열림 |

### 코드 변경 자체는 작다

SDK 가 세 가지 전송을 지원한다 — `stdio` / `sse` / `streamable-http`.
[확인: `MCPServer.run` 시그니처]

```python
def main() -> None:
    mcp.run(transport="streamable-http")     # run.py 한 줄
```

`sse` 는 구형이다. 새로 만들면 **`streamable-http`** 를 쓴다.
호스트·포트 등은 `run()` 의 `**kwargs` 로 넘어간다. [확인 필요: 인자 이름]

### 진짜 문제는 인증이다

stdio 에서는 `.env` 의 토큰이 곧 "이 PC 주인의 권한" 이었다.
서버로 옮기면 이 전제가 깨진다.

**서버에 토큰 하나를 박아두면, 접속한 모든 사람이 그 토큰 주인의 권한으로 Jira 를 본다.**
접근 가능한 다른 프로젝트가 100개 넘게 있으므로 이는 권한 누출이다.

선택지는 둘이다.

1. **서비스 계정 + 접속 인증.** 서버는 전용 계정 토큰을 쓰고,
   MCP 접속 자체를 인증으로 막는다. SDK 의 `auth` / `token_verifier` /
   `auth_server_provider` 파라미터가 이 자리다. [확인: `MCPServer.__init__` 시그니처]
   읽기 범위를 최소로 준 계정이어야 한다.
2. **사용자별 토큰 전달.** 요청마다 호출자의 Jira 토큰을 받아 쓴다.
   권한이 정확히 맞지만 토큰 보관·갱신 설계가 따로 필요하다.

**어느 쪽이든 `client.JiraClient` 를 프로세스 전역 싱글턴으로 두는 지금 구조를 바꿔야 한다.**
`server.py` 의 `_client` 전역 변수가 그것이다. 사용자별 토큰으로 가면
요청 단위로 클라이언트를 잡아야 한다.

### 그 밖에 챙길 것

- **`.env` 를 서버에 두지 않는다.** 시크릿 매니저나 환경변수 주입으로 바꾼다.
  `config.py` 는 이미 실제 환경변수를 `.env` 보다 우선한다.
- **TLS.** 평문 HTTP 로 토큰이 오가면 안 된다.
- **레이트 리밋.** 여러 사용자가 붙으면 Jira 429 를 맞기 쉽다.
  지금은 재시도 로직이 없다 — `_explain()` 이 안내만 한다.
- **로그.** stdout 금기는 stdio 전용 제약이다. HTTP 로 가면 stdout 로그가 정상이다.
  다만 **토큰과 이슈 본문이 로그에 남지 않게** 한다.
- **상태 점검 엔드포인트.** systemd/k8s 가 살아있는지 볼 방법이 필요하다.

---

## 4. 이전 전 체크리스트

- [ ] `requirements.txt` 버전 고정 유지
- [ ] 하드코딩된 Windows 경로 없는지 확인 (`D:\dev1` 검색)
- [ ] `docs/` 가 최신인지 — 새로 알아낸 것이 대화에만 남아 있지 않은지
- [ ] `.env` 가 저장소에 들어가지 않았는지 (`.gitignore` 확인)
- [ ] `smoke_test.py` 전체 PASS
- [ ] 인증 방식 결정 (위 3장)
- [ ] 서비스 계정 토큰 범위 최소화


---

## 5. 일일 에이전트를 클라우드 서버에 (NHN Cloud)

`agent/` 를 cron 으로 돌려 매일 솔루션 현황을 웹훅으로 보내는 구성.
**MCP 는 쓰지 않는다** — `requirements-agent.txt` 에 `mcp` 가 없는 이유다.

### 인스턴스 사양

워크로드가 **하루 1회 수 초**다. 상주 프로세스가 없고 cron 이 잠깐 띄웠다 끝낸다.
Jira REST 조회 약 2회 + Anthropic API 1회 + 웹훅 1회가 전부다.

| | 권장 | 최소 |
|---|---|---|
| vCPU | 1 | 1 |
| RAM | **2 GB** | 1 GB + swap 1 GB |
| 디스크 | 20 GB | 10 GB |

NHN Cloud 인스턴스 타입 이름은 콘솔에서 바뀌므로, **위 사양에 맞는 가장 작은 타입**을 고른다.

**왜 2GB 를 권하나.** 도는 동안 쓰는 메모리는 얼마 안 된다 — 같은 저장소의 MCP 서버
프로세스가 실측 40 MB 였고, 에이전트는 anthropic SDK(pydantic 포함)를 얹어도
**피크 150 MB 안쪽으로 예상된다** [추정: 실측은 서버 구축 후 `/usr/bin/time -v` 로 확인할 것].
2GB 가 필요한 건 실행 때가 아니라 **`pip install` 때**다. 1GB 에서는 휠이 없는 패키지를
빌드할 때 OOM 이 날 수 있다. 설치만 넘기면 1GB 로도 충분하니, 비용을 줄이려면
1GB + swap 으로 가도 된다.

디스크는 OS + venv(약 200 MB) + 로그다. 20GB 면 넉넉하고, 로그는 로테이션을 건다.

### OS 와 Python

- **Ubuntu 22.04 / 24.04 LTS** 기준으로 적는다.
- **Python 3.10 이상.** [확인] Ubuntu 22.04 기본 파이썬 3.10.12 에서 `anthropic==1.8.0`,
  `httpx==0.28.1`, `python-dotenv==1.2.3` 설치와 에이전트 실행까지 확인했다.
  로컬 개발은 3.14.3 이다. **배포판 기본 파이썬을 그대로 쓰면 되고** 새 버전을 올릴 필요 없다.
  (MCP 서버는 서버에 올리지 않으므로 `mcp` 패키지의 파이썬 요구사항은 고려 대상이 아니다.)

> **함정 — `python3-venv` 가 없으면 venv 는 만들어지는데 pip 이 없다.**
> `import venv` 는 통과하고 `python3 -m venv` 도 성공한 것처럼 보이지만,
> 그 안의 `bin/python -m pip` 이 `No module named pip` 으로 죽는다. ensurepip 이
> 별도 패키지이기 때문이다. 아래 설치 단계의 `python3-venv` 를 빠뜨리지 말 것.

### 네트워크

**인바운드는 필요 없다.** 서버가 먼저 나가기만 한다.

| 방향 | 대상 | 포트 |
|---|---|---|
| 아웃바운드 | `api.atlassian.com` (Jira) | 443 |
| 아웃바운드 | `api.anthropic.com` (요약) | 443 |
| 아웃바운드 | 웹훅 호스트 | 443 |
| 인바운드 | SSH — 사내 IP 만 | 22 |

보안 그룹에서 인바운드는 SSH 외 전부 막는다. 공인 IP 도 관리 목적이 아니면 붙일 이유가 없다.

### 설치

```bash
sudo apt update && sudo apt install -y git python3-venv python3-pip
sudo mkdir -p /opt/jira-mcp && sudo chown "$USER" /opt/jira-mcp
git clone https://github.com/YonggyuCho/jira-mcp-agent.git /opt/jira-mcp
cd /opt/jira-mcp

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-agent.txt
```

### `.env`

```
# Jira (기존 값 그대로)
JIRA_SITE=https://your-site.atlassian.net
JIRA_CLOUD_ID=<사이트의 cloud id>
JIRA_EMAIL=...
JIRA_API_TOKEN=...
JIRA_AUTH_MODE=gateway

# 요약 (없어도 리포트는 나간다 — 요약만 빠진다)
ANTHROPIC_API_KEY=...

# 발송
AGENT_WEBHOOK_URL=https://...
AGENT_WEBHOOK_FORMAT=teams     # slack | teams | teams-legacy | dooray | raw
AGENT_DRY_RUN=1                # 출력 확인 전까지는 1 로 둔다
AGENT_DUE_SOON_DAYS=3
AGENT_MODEL=claude-opus-5
AGENT_EFFORT=low               # 요약은 짧고 단순해서 low 로 충분하다
```

```bash
chmod 600 .env          # 토큰이 둘이나 들어 있다
```

#### Teams 웹훅 만들기

**Office 365 커넥터는 퇴역했다.** 지금 발급되는 것은 Workflows(Power Automate) 훅이고
받는 모양이 다르다 — 커넥터는 MessageCard, Workflows 는 Adaptive Card 다.

Teams 채널 > `...` > **워크플로** > "웹후크 요청을 받으면 채널에 게시" 템플릿으로 만든다.
나오는 URL 이 `https://...logic.azure.com/workflows/...` 모양이면 맞다.

`AGENT_WEBHOOK_FORMAT=teams` 가 이 방식이다. 옛 커넥터 훅
(`...webhook.office.com/webhookb2/...`)이 아직 살아 있다면 `teams-legacy` 를 쓴다.
설정과 URL 이 어긋나면 `notify.py` 가 URL 쪽을 따르고 경고를 남기므로,
조용히 실패하지는 않는다.

수락되면 Workflows 훅은 **HTTP 202** 를 돌려준다. 본문이 비어 있어도 정상이다.

**시크릿이 서버 파일로 존재하는 게 새로 생기는 위험이다.** 파일 권한을 600 으로 죄고,
가능하면 NHN Cloud 의 시크릿 관리나 환경변수 주입으로 옮긴다 —
`config.py` 는 실제 환경변수를 `.env` 보다 우선한다.

### 확인 후 발송 켜기

```bash
cd /opt/jira-mcp

# 1) 인증
.venv/bin/python check_auth.py

# 2) 아무것도 보내지 않고 내용만 확인
.venv/bin/python -m agent.daily

# 3) 이번 한 번만 실제 발송해 웹훅 확인
.venv/bin/python -m agent.daily --send

# 4) 결과가 맞으면 .env 에서 AGENT_DRY_RUN=0
```

### cron

```cron
# 평일 오전 9시
0 9 * * 1-5 cd /opt/jira-mcp && .venv/bin/python -m agent.daily >> /var/log/gpu-live.log 2>&1
```

- 서버 시간대를 먼저 확인한다 — `timedatectl set-timezone Asia/Seoul`.
- 로그 로테이션을 건다 (`/etc/logrotate.d/gpu-live`).
- **수집에 실패하면 발송하지 않고 exit 1 로 끝난다.** 알림이 안 온 날과
  "이상 없는 날" 을 구분하려면 이 종료 코드를 모니터링에 물린다.

### 운영 중 확인

```bash
tail -50 /var/log/gpu-live.log
```

- `WARNING ... 요약을 건너뜁니다` — 리포트는 정상 발송됐고 요약만 빠졌다. 급하지 않다.
- `ERROR 수집 실패` — **발송되지 않았다.** Jira 토큰 만료나 네트워크를 본다.
- `ERROR 발송 실패` — 취합은 됐고 웹훅이 거부했다. URL 과 포맷을 본다.
