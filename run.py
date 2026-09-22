"""stdio 진입점.

  python run.py

이 파일이 프로젝트 루트에 있어 파이썬이 루트를 sys.path 에 넣어주므로
어느 디렉토리에서 실행해도 jira_mcp 패키지를 찾는다.
"""
from jira_mcp.server import main

if __name__ == "__main__":
    main()
