"""`uv run python -m openbimagent` 入口:转发到 openbimagent.cli.main。"""

from openbimagent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
