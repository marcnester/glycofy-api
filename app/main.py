from fastapi import FastAPI
from app.routers.health import router as health_router

app = FastAPI(title="Glycofy API", version="0.1.0")
app.include_router(health_router, prefix="/health", tags=["health"])

@app.get("/")
def root():
    return {"message": "Glycofy API is running"}