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
    version="5.0.0",
    description="Manage service routing preferences and validate Mihomo, Surge and Shadowrocket subscriptions.",
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(health_router)
app.include_router(convert_router)
app.include_router(community_router)
app.include_router(system_router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/advanced", include_in_schema=False)
async def advanced() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.middleware("http")
async def private_response_cache_control(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/profiles", "/subscribe")) or request.url.path in {"/preview", "/check", "/diagnose", "/render", "/convert"}:
        response.headers["Cache-Control"] = "no-store"
    return response
