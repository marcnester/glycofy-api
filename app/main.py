# app/main.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Routers
from app.routers import (
    auth, users, activities, plans, recipes, imports,
    oauth_strava, oauth_google,  # <-- ensure oauth_google is imported
    summary
)

def create_app() -> FastAPI:
    app = FastAPI(title="Glycofy API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", summary="Root")
    def root():
        return {"message": "Glycofy API is running"}

    @app.get("/health", tags=["health"], summary="Health")
    def health():
        return {"status": "ok"}

    # API routers
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(activities.router, prefix="/activities", tags=["activities"])
    app.include_router(plans.router, prefix="/v1/plan", tags=["plan"])
    app.include_router(recipes.router, prefix="/recipes", tags=["recipes"])
    app.include_router(imports.router, prefix="/imports", tags=["imports"])
    app.include_router(oauth_strava.router, prefix="/oauth", tags=["oauth"])
    app.include_router(oauth_google.router, prefix="/oauth", tags=["oauth"])  # <-- mount Google
    app.include_router(summary.router, prefix="/v1", tags=["summary"])

    # UI static
    ui_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
    os.makedirs(ui_dir, exist_ok=True)
    app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

    @app.on_event("startup")
    async def _dump_routes():
        print("="*47, "Registered Routes", "="*47)
        for r in app.routes:
            methods = ",".join(sorted(getattr(r, "methods", []) or []))
            path = getattr(r, "path", "")
            name = getattr(r, "name", "")
            print(f"{methods:9s} {path:40s} {name}")
        print("="*110)

    return app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8090"))
    print(f"🚀 Starting Glycofy API on http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=True)