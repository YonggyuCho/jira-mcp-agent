# 조직 특정 문서에 대하여

이 저장소는 공개되어 있다. 따라서 **특정 조직의 운영 정보는 저장소에 넣지 않는다** —
담당자 실명, 업무 이메일, 고객사·사업명, 실제 이슈 키, Jira cloud id 같은 것들이다.

그런 내용은 `docs/internal/` 에 두고 `.gitignore` 로 제외한다.

`docs/internal` 은 실제 폴더일 수도, 다른 곳을 가리키는 심볼릭 링크일 수도 있다
(예: 서버에서 문서를 홈 디렉터리에 모아 두는 경우). `.gitignore` 가 `docs/internal`
처럼 **끝에 `/` 없이** 적혀 있는 이유다 — `docs/internal/` 로 쓰면 폴더에만 걸리고
링크는 걸리지 않아 커밋에 딸려 들어간다.

```
docs/internal/          # 저장소에 올라가지 않음
├── HANDOFF.md          현황과 남은 일 — 다른 기계에서 이어받을 때 먼저 읽는다
├── WORKING-RULES.md    사용자 상시 요구사항
└── GPU-LIVE-AUDIT.md   감사 정의, 제외 규칙, 기준선
```

## 다른 기계로 옮길 때

`git clone` 만으로는 `docs/internal/` 과 `.env` 가 따라가지 않는다. **둘을 따로 옮긴다.**

```bash
# 보내는 쪽 — -h 로 링크를 따라가 실제 파일을 담는다 (링크면 링크만 담긴다)
tar czhf private.tar.gz .env docs/internal

# 받는 쪽
scp private.tar.gz <서버>:/opt/jira-mcp/
ssh <서버> 'cd /opt/jira-mcp && tar xzf private.tar.gz && rm private.tar.gz && chmod 600 .env'
```

`.env` 에는 Jira 토큰과 Anthropic API 키가 들어 있다. **전송 경로와 파일 권한을 확인한다.**
사내 시크릿 관리 도구가 있으면 그쪽이 낫다 — `jira_mcp/config.py` 는 실제 환경변수를
`.env` 보다 우선하므로, 파일 없이 환경변수 주입만으로도 돌아간다.

## 새로 알아낸 것을 적을 때

판단 기준은 하나다 — **다른 조직 사람이 읽어도 무해한가.**

| 어디에 | 무엇을 |
|---|---|
| `docs/` (공개) | 도구의 구조·동작·함정, Jira API 의 일반적 성질, 배포 절차 |
| `docs/internal/` (비공개) | 사람 이름, 이메일, 고객사·사업명, 실제 이슈 키, cloud id, 사내 호스트 |

애매하면 `docs/internal/` 에 넣는다. 공개 저장소는 되돌리기 어렵다 —
지워도 GitHub 캐시와 포크에 남는다.
