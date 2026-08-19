import sys
import asyncio
import os
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.api import api_router
from app.db.init_db import init_db
from app.core.scheduler import process_issue_timeouts

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(process_issue_timeouts())
    yield
    task.cancel()

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

cors_origins_str = os.getenv("CORS_ORIGINS", "")
if cors_origins_str:
    cors_origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]
else:
    cors_origins = ["http://localhost:3000", "http://localhost:8001"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/")
def read_root():
    return {"message": f"Welcome to {settings.PROJECT_NAME} API"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

# Trigger reload
