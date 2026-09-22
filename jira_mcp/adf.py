"""ADF(Atlassian Document Format) -> 읽을 수 있는 평문.

Jira Cloud 의 description/comment 는 중첩 JSON 이라 그대로 모델에 주면
토큰만 먹고 읽기도 어렵다. 필요한 만큼만 눌러서 평문으로 만든다.
"""
from __future__ import annotations

from typing import Any


def to_text(node: Any, depth: int = 0) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(to_text(n, depth) for n in node)
    if not isinstance(node, dict):
        return str(node)

    kind = node.get("type")
    kids = node.get("content", [])

    if kind == "text":
        text = node.get("text", "")
        for mark in node.get("marks", []):
            if mark.get("type") == "code":
                text = f"`{text}`"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind == "paragraph":
        return to_text(kids, depth) + "\n"
    if kind == "heading":
        level = node.get("attrs", {}).get("level", 1)
        return f"\n{'#' * level} {to_text(kids, depth)}\n"
    if kind in {"bulletList", "orderedList"}:
        out = []
        for i, item in enumerate(kids, 1):
            bullet = "-" if kind == "bulletList" else f"{i}."
            body = to_text(item, depth + 1).strip()
            pad = "  " * depth
            out.append(f"{pad}{bullet} {body}")
        return "\n".join(out) + "\n"
    if kind == "listItem":
        return to_text(kids, depth)
    if kind == "codeBlock":
        lang = node.get("attrs", {}).get("language", "")
        return f"\n```{lang}\n{to_text(kids, depth)}\n```\n"
    if kind == "blockquote":
        body = to_text(kids, depth).strip()
        return "\n".join(f"> {line}" for line in body.splitlines()) + "\n"
    if kind == "rule":
        return "\n---\n"
    if kind == "mention":
        return "@" + node.get("attrs", {}).get("text", "").lstrip("@")
    if kind == "emoji":
        return node.get("attrs", {}).get("shortName", "")
    if kind == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    if kind in {"mediaSingle", "mediaGroup", "media"}:
        return "[첨부]"
    if kind == "table":
        return "\n" + to_text(kids, depth) + "\n"
    if kind == "tableRow":
        cells = [to_text(c, depth).strip().replace("\n", " ") for c in kids]
        return "| " + " | ".join(cells) + " |\n"
    if kind in {"tableCell", "tableHeader"}:
        return to_text(kids, depth)

    return to_text(kids, depth)


def flatten(node: Any, limit: int = 4000) -> str:
    """평문화 + 빈 줄 정리 + 길이 제한."""
    raw = to_text(node)
    lines = [ln.rstrip() for ln in raw.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln and out and not out[-1]:
            continue
        out.append(ln)
    text = "\n".join(out).strip()
    if len(text) > limit:
        text = text[:limit] + f"\n... (생략, 총 {len(text)}자)"
    return text
