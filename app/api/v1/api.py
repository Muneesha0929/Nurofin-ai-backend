from fastapi import APIRouter
from app.api.v1.endpoints import users, auth, tasks, meetings, projects, dashboard, notifications, planner, conversations, stream, workcenter, knowledge

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(meetings.router, prefix="/meetings", tags=["meetings"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(planner.router, prefix="/planner", tags=["planner"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(stream.router, prefix="/stream", tags=["stream"])
api_router.include_router(workcenter.router, prefix="/workcenter", tags=["workcenter"])
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
