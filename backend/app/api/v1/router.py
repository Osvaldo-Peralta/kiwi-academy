"""Router v1 — composición de todos los endpoints."""
from fastapi import APIRouter

from .courses import router as courses_router
from .lessons import router as lessons_router
from .exams import router as exams_router
from .progress import router as progress_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(courses_router)
api_router.include_router(lessons_router)
api_router.include_router(exams_router)
api_router.include_router(progress_router)
