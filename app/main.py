from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.community import router as community_router
from app.api.convert import router as convert_router
from app.api.health import router as health_router
from app.api.system import router as system_router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Subflow Strategy Builder",
    version="0.1.0",
    description="Build, analyze, simulate, and compile proxy policy workspaces into Mihomo configs.",
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(health_router)
app.include_router(convert_router)
app.include_router(community_router)
app.include_router(system_router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")
