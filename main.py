"""API 서버 진입점 (SPEC §1, DESIGN.md).

    uvicorn main:app --reload

`web.app.create_app` 팩토리로 FastAPI 앱을 생성한다. 기본값은 운영 구성(실제 Agent + DATABASE_URL).
"""

from __future__ import annotations

from web.app import create_app

app = create_app()


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8000")),
        reload=bool(os.environ.get("APP_RELOAD")),
    )
