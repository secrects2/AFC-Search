from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from src.web.api import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AFC 價格監控本機網站")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    project_root = Path(__file__).resolve().parent
    app = create_app(project_root)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
