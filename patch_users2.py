import re

with open('app/api/v1/endpoints/users.py', 'r') as f:
    content = f.read()

replacement = """
    # Build conflicts array
    conflicts = []
    from app.core.scheduling import format_conflict
    
    for m in conflicting_meetings:
        conflicts.append(format_conflict("meeting", m.id, "nurofin", m.title, m.start_time, m.end_time))
        
    for t in conflicting_tasks:
        conflicts.append(format_conflict("task", t.id, "nurofin", t.title, t.scheduled_start_time, t.scheduled_end_time))
        
    is_busy = len(conflicting_meetings) > 0 or len(conflicting_tasks) > 0
    
    reasons = []
    if conflicting_meetings:
        reasons.append("busy with meetings")
    if conflicting_tasks:
        reasons.append("busy working on a task")
        
    # Check Google Calendar
    conflicting_google = []
    busy_blocks_google = []
    try:
        from datetime import datetime
        import pytz
        
        req_start_dt = datetime.strptime(start_time, "%H:%M").time()
        req_end_dt = datetime.strptime(end_time, "%H:%M").time()
        
        target_user_res = await db.execute(select(User).filter(User.id == user_id))
        target_user = target_user_res.scalars().first()
        if target_user and target_user.google_access_token:
            from app.services.google_calendar import fetch_calendar_events
            time_min = datetime.fromisoformat(date + "T00:00:00+00:00")
            time_max = datetime.fromisoformat(date + "T23:59:59+00:00")
            g_events = fetch_calendar_events(target_user, time_min, time_max)
            tz_kolkata = pytz.timezone('Asia/Kolkata')
            
            for item in g_events:
                # Skip transparent (free) events
                if item.get('transparency') == 'transparent':
                    continue
                    
                st_dt_str = item['start'].get('dateTime')
                et_dt_str = item['end'].get('dateTime')
                st_date_str = item['start'].get('date')
                
                # Handle all-day events
                if st_date_str and not st_dt_str:
                    # All-day event marked as busy blocks the whole day
                    conflicts.append({
                        "type": "google_event",
                        "id": item.get('id'),
                        "source": "google_calendar",
                        "title": item.get('summary', 'Busy'),
                        "start_time": "00:00",
                        "end_time": "23:59"
                    })
                    conflicting_google.append(item)
                    busy_blocks_google.append({"start": "00:00", "end": "23:59"})
                    continue
                    
                if st_dt_str and et_dt_str:
                    try:
                        # Parse with timezone awareness
                        b_s_dt = datetime.fromisoformat(st_dt_str.replace('Z', '+00:00'))
                        b_e_dt = datetime.fromisoformat(et_dt_str.replace('Z', '+00:00'))
                        
                        # Convert to Asia/Kolkata
                        b_s_local = b_s_dt.astimezone(tz_kolkata)
                        b_e_local = b_e_dt.astimezone(tz_kolkata)
                        
                        # Compare dates (in case event spans multiple days in KolKata time)
                        if b_s_local.strftime('%Y-%m-%d') == date:
                            b_s = b_s_local.time()
                            b_e = b_e_local.time()
                            
                            # Standard overlap
                            if req_start_dt < b_e and req_end_dt > b_s:
                                conflicting_google.append(item)
                                conflicts.append({
                                    "type": "google_event",
                                    "id": item.get('id'),
                                    "source": "google_calendar",
                                    "title": item.get('summary', 'Busy'),
                                    "start_time": b_s_local.strftime('%H:%M'),
                                    "end_time": b_e_local.strftime('%H:%M')
                                })
                            
                            busy_blocks_google.append({
                                "start": b_s_local.strftime('%H:%M'),
                                "end": b_e_local.strftime('%H:%M')
                            })
                    except Exception as e:
                        print(f"Failed parsing google event: {e}")
                        pass
    except Exception as e:
        print(f"Error checking google calendar: {e}")
        pass

    if conflicting_google:
        is_busy = True
        reasons.append("busy with Google Calendar event")
        
    status_color = "red" if is_busy else "green"
"""

start_idx = content.find("is_busy = len(conflicting_meetings) > 0")
end_idx = content.find("alternative_times = []")

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + replacement + content[end_idx:]
    
    # Also update the response payload
    content = content.replace(
        'data={"is_busy": is_busy, "reasons": reasons, "status_color": status_color, "alternative_times": alternative_times}',
        'data={"is_busy": is_busy, "reasons": reasons, "conflicts": conflicts, "status_color": status_color, "alternative_times": alternative_times}'
    )
    
    with open('app/api/v1/endpoints/users.py', 'w') as f:
        f.write(content)
    print("Patched check_availability")
else:
    print("Could not find patch points")
