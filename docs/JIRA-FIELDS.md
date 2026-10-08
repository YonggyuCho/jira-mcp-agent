# Jira 커스텀 필드 레퍼런스

이 문서의 필드 ID 는 **특정 Jira 사이트에서 확인한 값**이다. 다른 사이트에서는
3장의 방법으로 다시 조회해야 한다. 1장의 JQL 함정은 Jira Cloud 공통이다.

최종 확인: 2026-09-30

---

## 1. 핵심 — 한글 필드명은 JQL 에서 쓸 수 없다

Jira 는 **동명(同名) 커스텀 필드가 여럿이면 이름을 해석하지 못한다.**
이 사이트의 필드는 총 343개이고, 그중 이름이 겹치는 것이 있다.

| 이름 | 같은 이름의 필드 수 |
|---|---|
| `솔루션` | **6개** (12473, 12605, 12606, 12607, 12608, 12893) |
| `고객사명` | 2개 (10172, 12595) |
| `엔지니어` | 3개 (10991, 12414, 12820) |
| `엔지니어 지원 방법` | 2개 (12428, 12819) |
| `시작일` | 2개 (10193, 12569) — `시작 날짜`(10015)와는 또 다른 필드 |

그래서 이름으로 질의하면 실패하는데, **에러가 아니라 빈 결과(0건)를 돌려준다.**

```
project = PROJ AND "솔루션" is not EMPTY       → 0건    ← 질의가 틀린 것
project = PROJ AND cf[12473] is not EMPTY     → 정상
```

### 검증 방법

없는 필드명을 넣어 보면 이 동작을 직접 확인할 수 있다.

```
project = PROJ AND "존재하지않는필드ABC" is not EMPTY   → 0건 (에러 아님)
```

**따라서 "0건" 을 곧이곧대로 믿으면 안 된다.**
결과가 0건이면 `cf[ID]` 형태로 바꿔 한 번 더 확인한 뒤에 결론을 낸다.

### 서버의 자동 치환

`jira_mcp/server.py` 의 `JQL_ALIASES` 가 자주 쓰는 이름을 `cf[ID]` 로 바꿔 준다.
연산자가 뒤따를 때만 치환하므로 **검색어로 쓴 같은 낱말은 건드리지 않는다.**

```
솔루션 = "제품A"          →  cf[12473] = "제품A"     (필드 자리 → 치환)
text ~ "솔루션"            →  그대로                      (값 자리 → 유지)
```

치환표에 없는 필드는 직접 `cf[ID]` 로 적는다.

---

## 2. 필드 ID 표

### 서버가 다루는 필드 (`server.py` 의 `CUSTOM_FIELDS`)

| 필드 | ID | 형태 | 값 |
|---|---|---|---|
| 솔루션 | `customfield_12473` | 다중선택 (리스트) | `제품A`, `제품B` 등 |
| 고객사명(통합키) | `customfield_12487` | 다중선택 (리스트) | `고객사A` 등 |
| Actual start | `customfield_10008` | 날짜시간 | `2026-09-07T10:00:00.000+0900` |
| Actual end | `customfield_10009` | 날짜시간 | 〃 |
| 일정 준수 여부 | `customfield_12594` | 선택 | `Early` / `On Time` / `Delayed` / `Not Evaluated` |
| 지연 사유 | `customfield_12604` | 선택 | |

`Actual start` / `Actual end` 는 **영문명이라 JQL 에서 이름 그대로도 동작한다.**
`"Actual end" > "2026-09-10 23:59"` 처럼 쓸 수 있다.

### 기타 확인된 필드

| 필드 | ID |
|---|---|
| Expected Start Date | `customfield_10197` |
| 고객사명 | `customfield_10172`, `customfield_12595` |
| 고객사 종류 | `customfield_12478` |
| 고객사 담당자 | `customfield_12822` |
| 요청 경로 | `customfield_12480` |
| 엔지니어 | `customfield_12414` — 동명 3개(10991, 12414, 12820) 중 실제로 쓰이는 것 |
| 엔지니어 지원 방법 | `customfield_12428` — 동명 2개(12428, 12819) 중 실제로 쓰이는 것. 값: `본사` / `원격` 등 |
| 참조 | `customfield_12427` — 동명·유사 4개(참조자 10170·12707, 참조(CC) 11857) 중 회의 유형에서 실제로 쓰이는 것 |
| 시작 날짜 | `customfield_10015` — `시작일`(10193, 12569)과 다른 필드 |
| 이슈 분류 | `customfield_12530` — 선택. 값 예: `고객요청`(띄어쓰기 없음), `솔루션 제품 결함`, `솔루션 제품 개선` |
| 권역 | `customfield_12472` — 다중선택 `[{"value":"서울"}]` |
| 요청 경로 | `customfield_12480` — 선택 `{"value":"고객"}` |
| 고객사명(통합키) | `customfield_12487` — 문자열 배열 `["고객사A"]` (선택지 없음, 레이블처럼) |
| 연락 담당자 | `customfield_12477` |
| 접속 정보 | `customfield_12479` |
| 순위 | `customfield_10019` |

### 시스템 필드

`duedate` 는 Jira 화면에서 **"기한"** 으로 표시된다. `read_issue` 는 "마감일" 로 찍는다.
셋이 같은 값이다.

---

## 3. 필드를 새로 찾는 법

```python
import sys; sys.path.insert(0, ".")
from jira_mcp import client, config
c = client.JiraClient(config.load())

# 전체 필드에서 이름으로 검색
for f in c.get("/field"):
    if "일정" in f.get("name", ""):
        print(f["id"], f["custom"], f["name"])

# 특정 이슈에 실제로 담긴 값 전부 보기 (비어 있지 않은 것만)
issue = c.get("/issue/PROJ-123", params={"fields": "*all"})
for k, v in issue["fields"].items():
    if v not in (None, [], {}, "") and k.startswith("customfield_"):
        print(k, repr(v)[:100])
```

Windows 에서는 `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` 을 붙여 실행한다.

**화면에 보이는데 API 응답에 없다면 권한 문제가 아니라 요청에 필드를 안 넣은 것이다.**
`fields` 파라미터에 명시하지 않은 커스텀 필드는 Jira 가 돌려주지 않는다.
`fields=*all` 로 확인해 보면 바로 갈린다.

---

## 4. 값 해석 주의

필드 형태마다 JSON 모양이 다르다. `server.py` 의 `_val()` 이 이걸 흡수한다.

| 형태 | JSON | 뽑을 키 |
|---|---|---|
| 선택 | `{"value": "On Time", "id": ...}` | `value` |
| 사용자 | `{"displayName": "홍길동", ...}` | `displayName` |
| 상태·유형 | `{"name": "종료", ...}` | `name` |
| 다중선택 | `[{"value": "제품A"}]` | 리스트 순회 |
| 날짜시간 | `"2026-09-07T10:00:00.000+0900"` | 앞 10자 |

---

## 5. 검색 API 주의

- 구 엔드포인트 `/rest/api/3/search` 는 **HTTP 410 Gone**. `/search/jql` 만 쓴다.
- 페이지네이션이 `startAt` 이 아니라 **`nextPageToken` / `isLast`** 다.
  `startAt` 을 보내면 `HTTP 400 Invalid request payload` 가 난다.
- 응답에 `total` 이 없다. 전체 건수를 알려면 끝까지 넘겨야 한다.
- 제약 없는 JQL(`order by ...` 만)은 거부된다. `server.py` 가 `updated >= -90d` 를 씌운다.

---

## 6. 이슈 만들 때 (createmeta)

`GET /issue/createmeta/<프로젝트>/issuetypes` → 유형 목록, `…/issuetypes/<id>` → 그 유형의 필드·필수 여부·선택지.
읽기 범위 토큰으로도 조회된다. 같은 이름의 유형이 둘일 수 있다(이 사이트의 '회의' 는 일반·하위 두 개) — `subtask` 값으로 구분한다.
하위 유형(하위 작업·기술지원·회의)은 `parent` 가 필수다.
사용자 필드는 `{"accountId": ...}` 로 보낸다. 날짜시간은 `2026-10-08T09:00:00.000+0900`, 설명은 ADF.
