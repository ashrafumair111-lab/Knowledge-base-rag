"""Static file cache headers.

Note: ye answer/query cache NAHI hai - sirf HTTP header set karta hai taake browser
purana app.js na chalaye.
"""

from starlette.requests import Request


async def no_cache_static(request: Request, call_next):
    """Static files browser cache me stale na rahein.

    Warna purana app.js chal jata hai, X-Session-Id header nahi bhejta, aur saare
    visitors ek hi "default" session me chale jate hain.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response
