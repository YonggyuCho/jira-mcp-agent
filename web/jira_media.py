"""붙여넣은 사진 → Jira 첨부 → 본문(댓글·설명) 안에 보이게. 그리고 평문 → ADF 변환.

Jira 본문에는 사진 데이터를 직접 넣을 수 없다. 본문의 media 노드가 '이 이슈의 첨부' 를 가리킨다
(Jira 화면에서 붙여넣어도 똑같이 첨부로 올라간다). 그래서 순서는:

  1. POST /issue/{key}/attachments (multipart, X-Atlassian-Token: no-check) → 첨부 id
  2. GET /attachment/content/{id} 를 **리다이렉트를 따라가지 않고** 보낸다 → Location 의
     https://api.media.atlassian.com/file/<UUID>/binary 에서 UUID 를 꺼낸다 (2026-10-15 실측:
     Jira 가 붙여넣은 사진의 media id 와 같은 값)
  3. ADF {"type":"mediaSingle", content:[{"type":"media","attrs":{"type":"file","id":UUID,"collection":""}}]}

글 속 `[사진 n]` 자리에 n 번째 사진이 들어간다. 자리 표시가 없는 사진은 맨 끝에 붙인다.
공용 클라이언트(jira_mcp.client)에 메서드를 늘리지 않는다 — MCP 는 읽기 전용이라 쓰기 코드는 web 쪽에만 둔다.
"""
from __future__ import annotations

import base64
import re
import secrets
import time

from jira_mcp import client

MAX_IMAGES = 5
MAX_BYTES = 10 * 1024 * 1024
TOKEN = re.compile(r"\[사진 (\d+)\]")
URL = re.compile(r"https?://[^\s<>\"]+")
MEDIA_ID = re.compile(r"/file/([0-9a-f-]{36})/")
MAGIC = [(b"\x89PNG\r\n\x1a\n", "image/png", "png"), (b"\xff\xd8\xff", "image/jpeg", "jpg"),
         (b"GIF87a", "image/gif", "gif"), (b"GIF89a", "image/gif", "gif")]


# ── 사진 확인 ──────────────────────────────────────────────────────────────

def decode(images: list[dict]) -> tuple[list[dict], str | None]:
    """화면이 보낸 [{n, data(base64 또는 data URL)}] → [{n, bytes, mime, ext}]. 형식은 파일 앞부분으로 판단한다."""
    if len(images) > MAX_IMAGES:
        return [], f"사진은 한 번에 {MAX_IMAGES}장까지입니다 (지금 {len(images)}장)"
    out = []
    for img in images:
        data = str(img.get("data") or "")
        data = data.split(",", 1)[1] if data.startswith("data:") else data
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError:
            return [], f"사진 {img.get('n')} 을 읽지 못했습니다"
        if len(raw) > MAX_BYTES:
            return [], f"사진 {img.get('n')} 이 {len(raw) / 1048576:.1f}MB — 장당 {MAX_BYTES // 1048576}MB 까지입니다"
        kind = next(((m, e) for sig, m, e in MAGIC if raw.startswith(sig)), None)
        if not kind:
            return [], f"사진 {img.get('n')} 은 PNG·JPG·GIF 가 아닙니다"
        out.append({"n": int(img.get("n") or len(out) + 1), "bytes": raw, "mime": kind[0], "ext": kind[1]})
    return out, None


def check_meta(images: list[dict]) -> str | None:
    """Dry-run 용 — 내용 없이 {n, size, type} 만으로 확인."""
    if len(images) > MAX_IMAGES:
        return f"사진은 한 번에 {MAX_IMAGES}장까지입니다 (지금 {len(images)}장)"
    for img in images:
        if int(img.get("size") or 0) > MAX_BYTES:
            return f"사진 {img.get('n')} 이 장당 {MAX_BYTES // 1048576}MB 를 넘습니다"
        if img.get("type") not in {m for _, m, _ in MAGIC}:
            return f"사진 {img.get('n')} 은 PNG·JPG·GIF 가 아닙니다"
    return None


# ── 올리기 ─────────────────────────────────────────────────────────────────

def _upload(c: client.JiraClient, key: str, img: dict) -> str:
    """첨부 하나 올리고 첨부 id. 공용 클라이언트의 기본 Content-Type(JSON)을 피하려고 multipart 를 직접 만든다."""
    _, base = c.resolve_base()
    name = f"paste-{time.strftime('%Y%m%d-%H%M%S')}-{img['n']}-{secrets.token_hex(3)}.{img['ext']}"
    boundary = "----gpulive" + secrets.token_hex(12)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: {img['mime']}\r\n\r\n").encode() + img["bytes"] + f"\r\n--{boundary}--\r\n".encode()
    r = c._http.post(f"{base}/issue/{key}/attachments", content=body,
                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Atlassian-Token": "no-check"})
    if r.status_code >= 400:
        raise client.JiraError(f"사진 {img['n']} 올리기 → HTTP {r.status_code}: {r.text[:200]}")
    return str(r.json()[0]["id"])


def _media_id(c: client.JiraClient, att_id: str) -> str:
    _, base = c.resolve_base()
    r = c._http.get(f"{base}/attachment/content/{att_id}", follow_redirects=False)
    m = MEDIA_ID.search(r.headers.get("location", ""))
    if not m:
        raise client.JiraError(f"첨부 {att_id} 의 media id 를 찾지 못했습니다 (HTTP {r.status_code})")
    return m.group(1)


def upload_all(c: client.JiraClient, key: str, imgs: list[dict], done: list[str]) -> dict[int, str]:
    """사진들을 올리고 {n: media UUID}. 올린 것은 done 에 남긴다 (중간 실패 때 무엇이 남았는지 알리려고)."""
    media = {}
    for img in imgs:
        att = _upload(c, key, img)
        done.append(f"사진 {img['n']} 첨부 (id {att})")
        media[img["n"]] = _media_id(c, att)
    return media


# ── 평문 → ADF ─────────────────────────────────────────────────────────────

def _inline(line: str) -> list[dict]:
    """한 줄 → text 노드. 주소는 링크로 만든다 — API 로 넣은 글은 Jira 가 자동으로 링크를 걸지 않는다."""
    out, pos = [], 0
    for m in URL.finditer(line):
        url = m.group(0).rstrip(".,)")
        if m.start() > pos:
            out.append({"type": "text", "text": line[pos:m.start()]})
        out.append({"type": "text", "text": url, "marks": [{"type": "link", "attrs": {"href": url}}]})
        pos = m.start() + len(url)
    if pos < len(line):
        out.append({"type": "text", "text": line[pos:]})
    return out


def _para(lines: list[str]) -> dict | None:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    if not lines:
        return None
    lines = [lines[0].lstrip()] + lines[1:-1] + [lines[-1].rstrip()] if len(lines) > 1 else [lines[0].strip()]
    content = []
    for i, line in enumerate(lines):
        if i:
            content.append({"type": "hardBreak"})
        content += _inline(line)
    return {"type": "paragraph", "content": content}


def _image(uuid: str) -> dict:
    return {"type": "mediaSingle", "attrs": {"layout": "align-start"},
            "content": [{"type": "media", "attrs": {"type": "file", "id": uuid, "collection": ""}}]}


def to_adf(text: str, media: dict[int, str] | None = None) -> dict:
    """평문 → ADF. 빈 줄 = 문단, 줄바꿈 = hardBreak, 주소 = 링크, `[사진 n]` = 그 자리에 사진."""
    media = dict(media or {})
    nodes: list[dict] = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        # 토큰 기준으로 문단을 끊는다 (사진은 문단 안에 못 들어가는 블록 노드)
        pieces = TOKEN.split(block)  # [글, n, 글, n, 글 ...]
        for i, piece in enumerate(pieces):
            if i % 2 == 0:
                p = _para(piece.split("\n"))
                if p:
                    nodes.append(p)
            else:
                uuid = media.pop(int(piece), None)
                if uuid:
                    nodes.append(_image(uuid))
    nodes += [_image(u) for _, u in sorted(media.items())]  # 자리 표시를 지운 사진은 끝에
    return {"type": "doc", "version": 1, "content": nodes or [{"type": "paragraph", "content": []}]}


def strip_tokens(text: str) -> str:
    return TOKEN.sub("", text or "")
