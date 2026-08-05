from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.database import engine
from app.suppliers.router import router as suppliers_router

app = FastAPI(
    title="供应商风险监控平台",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.include_router(suppliers_router)


def check_database() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


@app.get("/api/v1/system/health")
def health(response: Response) -> dict[str, str]:
    try:
        check_database()
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unavailable"}
    return {"status": "ok", "database": "ok"}


static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        candidate = (static_dir / path).resolve()
        if candidate.is_file() and static_dir in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
