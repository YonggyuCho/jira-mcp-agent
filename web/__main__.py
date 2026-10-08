"""웹 화면 실행기. `.env` 의 WEB_UI 로 화면 판을 고른다.

  python -m web            # 기본 = 현재 화면 (web/server.py)
  WEB_UI=v1 python -m web  # 백업 = 디자인 개편 전 화면 (web/server_v1.py)

되돌리기: .env 에 WEB_UI=v1 을 넣고 `sudo systemctl restart gpu-live-web`.
"""
import importlib
import os
import sys

from jira_mcp import config as _cfg  # noqa: F401  (.env 를 읽는다)

ui = os.environ.get("WEB_UI", "").strip().lower()
mod = importlib.import_module("web.server_v1" if ui == "v1" else "web.server")
print(f"web ui: {mod.__name__}", file=sys.stderr)
mod.main()
