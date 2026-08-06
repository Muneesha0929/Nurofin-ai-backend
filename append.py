import sys

content = """
@router.get("/availability/today", response_model=APIResponse)
async def check_team_availability_today(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    # Get today's date string
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Get all active users
    users_res = await db.execute(select(User).filter(User.is_deleted == False))
    users = users_res.scalars().all()
    user_ids = [u.id for u in users]
    
    if not user_ids:
        return success_response(data={}, message="No users found")
    
    # 1. Check Meetings for today
    meetings_res = await db.execute(
        select(MeetingParticipant.user_id).join(Meeting).filter(
            MeetingParticipant.user_id.in_(user_ids),
            MeetingParticipant.status != ParticipantStatusEnum.declined,
            Meeting.date == today_str,
            Meeting.is_deleted == False
        )
    )
    meeting_counts = {}
    for row in meetings_res:
        uid = row[0]
        meeting_counts[uid] = meeting_counts.get(uid, 0) + 1
        
    # 2. Check Tasks for today
    tasks_res = await db.execute(
        select(Task.assigned_to_id).filter(
            Task.assigned_to_id.in_(user_ids),
            Task.status != 'completed',
            or_(
                Task.scheduled_date == today_str,
                Task.due_date == today_str
            )
        )
    )
    task_counts = {}
    for row in tasks_res:
        uid = row[0]
        if uid:
            task_counts[uid] = task_counts.get(uid, 0) + 1
            
    availability_map = {}
    for u in users:
        total_events = meeting_counts.get(u.id, 0) + task_counts.get(u.id, 0)
        
        if total_events == 0:
            status = 'free'
            color = 'green'
        elif total_events <= 2:
            status = 'partial'
            color = 'orange'
        else:
            status = 'busy'
            color = 'red'
            
        availability_map[u.id] = {
            "status": status,
            "color": color,
            "count": total_events
        }
        
    return success_response(
        data=availability_map,
        message="Team availability fetched successfully"
    )
"""

with open(r"C:\Users\Muneesha\Desktop\Nurofin Executive AI\nurofin-ai-backend\app\api\v1\endpoints\users.py", "a") as f:
    f.write(content)
