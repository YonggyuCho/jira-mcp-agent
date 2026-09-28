"""MCP 규격대로 서버를 구동해 도구를 호출하는 스모크 테스트.

서버를 자식 프로세스로 띄우고 stdio 로 JSON-RPC 를 주고받는다.
Claude Code 없이 "붙는지 / 도구가 도는지 / 실패가 깔끔한지" 를 확인한다.

  .venv\\Scripts\\python.exe smoke_test.py
"""
from __future__ import annotations

import json
import pathlib
import queue
import subprocess
import sys
import threading

TIMEOUT = 40  # 응답 대기 상한(초). 넘으면 매달리지 않고 실패로 처리한다.

ROOT = pathlib.Path(__file__).resolve().parent
# venv 인터프리터 경로는 OS 마다 다르다. 둘 다 찾아보고 없으면 현재 인터프리터.
PY = next(
    (c for c in (ROOT / ".venv" / "Scripts" / "python.exe",
                 ROOT / ".venv" / "bin" / "python") if c.exists()),
    pathlib.Path(sys.executable),
)

PASS, FAIL = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}", flush=True)
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" - {detail}" if detail else ""), flush=True)


class Server:
    """서버 프로세스를 띄우고 JSON-RPC 를 주고받는다."""

    def __init__(self) -> None:
        self.p = subprocess.Popen(
            [str(PY), "-u", str(ROOT / "run.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, cwd=str(ROOT),
        )
        self._id = 0
        self._out: queue.Queue[str | None] = queue.Queue()
        self.stderr: list[str] = []
        # 양쪽 파이프를 계속 비운다. stderr 를 안 읽으면 버퍼가 차서 서버가 멈춘다.
        threading.Thread(target=self._pump, args=(self.p.stdout, self._out), daemon=True).start()
        threading.Thread(target=self._drain, args=(self.p.stderr,), daemon=True).start()

    @staticmethod
    def _pump(stream, q: queue.Queue) -> None:
        for line in stream:
            q.put(line)
        q.put(None)

    def _drain(self, stream) -> None:
        for line in stream:
            self.stderr.append(line.rstrip())

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        while True:
            try:
                line = self._out.get(timeout=TIMEOUT)
            except queue.Empty:
                raise TimeoutError(f"{method}: {TIMEOUT}초 안에 응답 없음") from None
            if line is None:
                raise RuntimeError(f"{method}: 서버가 종료됨. stderr:\n" + "\n".join(self.stderr[-15:]))
            line = line.strip()
            if not line:
                continue
            got = json.loads(line)
            if got.get("id") == self._id:
                return got

    def notify(self, method: str) -> None:
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.p.stdin.flush()

    def tool(self, name: str, **args) -> tuple[str, bool]:
        """(텍스트, 에러여부) 를 돌려준다."""
        r = self.call("tools/call", {"name": name, "arguments": args})
        if "error" in r:
            return json.dumps(r["error"], ensure_ascii=False), True
        res = r["result"]
        text = "\n".join(c.get("text", "") for c in res.get("content", []))
        return text, bool(res.get("isError"))

    def close(self) -> None:
        self.p.stdin.close()
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def main() -> int:
    print(f"서버: {PY.name} run.py\n")
    s = Server()

    # --- 1. 핸드셰이크 ---------------------------------------------------
    print("1. 핸드셰이크")
    r = s.call("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "smoke_test", "version": "1"},
    })
    info = r["result"]["serverInfo"]
    check("initialize 응답", r.get("result") is not None)
    check("서버 이름이 jira", info.get("name") == "jira", str(info))
    check("instructions 존재", bool(r["result"].get("instructions")))
    s.notify("notifications/initialized")

    # --- 2. 도구 목록 -----------------------------------------------------
    print("\n2. 도구 목록")
    tools = {t["name"]: t for t in s.call("tools/list")["result"]["tools"]}
    check("도구 3개", len(tools) == 3, f"실제 {len(tools)}개: {list(tools)}")
    for name in ("whoami", "search_issues", "read_issue"):
        check(f"{name} 노출", name in tools)
    check("전부 readOnly 표시",
          all((t.get("annotations") or {}).get("readOnlyHint") for t in tools.values()))
    check("search_issues 설명에 JQL 제약 안내",
          "제약" in tools["search_issues"]["description"])

    # --- 3. 정상 경로 -----------------------------------------------------
    print("\n3. 정상 경로")
    text, err = s.tool("whoami")
    check("whoami 성공", not err, text[:120])
    check("whoami 에 계정 정보", "accountId" in text, text[:120])

    text, err = s.tool("search_issues",
                       jql="assignee = currentUser() ORDER BY updated DESC", limit=5)
    check("search_issues 성공", not err, text[:160])
    keys = [ln.split(" | ")[0] for ln in text.splitlines()[1:] if " | " in ln]
    check("결과 행 파싱됨", len(keys) > 0, text[:160])

    text, err = s.tool("search_issues", jql="ORDER BY updated DESC", limit=3)
    check("제약 없는 JQL 자동 보정", not err, text[:160])

    if keys:
        text, err = s.tool("read_issue", key=keys[0])
        check(f"read_issue({keys[0]}) 성공", not err, text[:160])
        check("본문 구분선 포함", "본문" in text, text[:160])
        check("링크 포함", "browse/" in text, text[:160])

    # --- 4. 실패 경로 -----------------------------------------------------
    print("\n4. 실패 경로 (깔끔하게 실패하는지)")
    text, err = s.tool("read_issue", key="NOPE-99999")
    check("없는 이슈 → 에러로 반환", err, "에러가 아니라 성공으로 왔다")
    check("에러에 상태코드 포함", "404" in text or "400" in text, text[:200])
    check("에러에 조치 안내 포함", "확인하세요" in text or "확인" in text, text[:200])

    text, err = s.tool("search_issues", jql="이건 = JQL이 아님", limit=3)
    check("잘못된 JQL → 에러로 반환", err, "에러가 아니라 성공으로 왔다")
    check("JQL 에러에 조치 안내", "JQL" in text, text[:200])

    text, err = s.tool("whoami")
    check("실패 후에도 서버 살아있음", not err, text[:120])

    s.close()

    print(f"\n{'=' * 46}\n  PASS {PASS}  /  FAIL {FAIL}\n{'=' * 46}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
