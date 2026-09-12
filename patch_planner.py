import re

with open('app/api/v1/endpoints/planner.py', 'r') as f:
    content = f.read()

replacement = """
    # Process local meetings
    from app.core.scheduling import is_overlapping
    
    for m in local_meetings:
        if start_time and end_time:
            if not is_overlapping(start_time, end_time, m.start_time, m.end_time):
                continue
        for p in m.participant_entries:
            if p.user_id in users_map:
                busy_blocks.append({
                    "user_id": p.user_id,
                    "user_name": users_map[p.user_id].full_name,
                    "source": "nurofin",
                    "title": m.title,
                    "start_time": m.start_time,
                    "end_time": m.end_time,
                })

    # Process Google Calendar
    import pytz
    tz_kolkata = pytz.timezone('Asia/Kolkata')
    
    for uid, user in users_map.items():
        if user.google_access_token and user.google_refresh_token:
            try:
                google_events = fetch_calendar_events(user, time_min, time_max)
                for item in google_events:
                    if item.get('transparency') == 'transparent':
                        continue
                        
                    st_dt_str = item['start'].get('dateTime')
                    et_dt_str = item['end'].get('dateTime')
                    st_date_str = item['start'].get('date')
                    
                    if st_date_str and not st_dt_str:
                        # All day event
                        b_s_str, b_e_str = "00:00", "23:59"
                        
                        if start_time and end_time:
                            if not is_overlapping(start_time, end_time, b_s_str, b_e_str):
                                continue
                                
                        busy_blocks.append({
                            "user_id": uid,
                            "user_name": user.full_name,
                            "source": "google_calendar",
                            "title": item.get('summary', 'Busy'),
                            "start": b_s_str,
                            "end": b_e_str,
                        })
                        continue
                        
                    if st_dt_str and et_dt_str:
                        b_s_dt = datetime.fromisoformat(st_dt_str.replace('Z', '+00:00')).astimezone(tz_kolkata)
                        b_e_dt = datetime.fromisoformat(et_dt_str.replace('Z', '+00:00')).astimezone(tz_kolkata)
                        
                        b_s_str = b_s_dt.strftime('%H:%M')
                        b_e_str = b_e_dt.strftime('%H:%M')
                        
                        if start_time and end_time:
                            if not is_overlapping(start_time, end_time, b_s_str, b_e_str):
                                continue
                                
                        busy_blocks.append({
                            "user_id": uid,
                            "user_name": user.full_name,
                            "source": "google_calendar",
                            "title": item.get('summary', 'Busy'),
                            "start": b_s_str,
                            "end": b_e_str,
                        })
            except Exception as e:
                err_str = str(e).lower()
                is_auth_error = "invalid_grant" in err_str or "token has been expired or revoked" in err_str or "refresh_token" in err_str or "refresherror" in err_str
                if is_auth_error:
                    user.google_access_token = None
                    user.google_refresh_token = None
                    user.google_token_expires_at = None
"""

start_idx = content.find("    # Process local meetings")
end_idx = content.find("    return success_response(")

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + replacement + content[end_idx:]
    with open('app/api/v1/endpoints/planner.py', 'w') as f:
        f.write(content)
    print("Patched check_availability in planner")
else:
    print("Could not find patch points")
