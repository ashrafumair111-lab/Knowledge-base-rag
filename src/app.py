"""FastAPI assembly: middleware, static mount aur routers (Node ke app.js jaisa)."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import ASSETS_DIR
from src.middleware.session import session_cookie
from src.middleware.static_headers import no_cache_static
from src.routes import chat, files, pages, upload

app = FastAPI()

# Static files (CSS/JS) - browser cache stale na ho, phir session cookie
app.middleware("http")(no_cache_static)
app.middleware("http")(session_cookie)

app.mount("/static", StaticFiles(directory=str(ASSETS_DIR)), name="static")

app.include_router(pages.router)
app.include_router(files.router)
app.include_router(upload.router)
app.include_router(chat.router)
