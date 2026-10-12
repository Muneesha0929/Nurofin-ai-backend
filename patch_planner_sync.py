import re

with open('app/api/v1/endpoints/planner.py', 'r', encoding='utf-8') as f:
    content = f.read()

outlook_sync_block = """
    if target_user.outlook_access_token and target_user.outlook_refresh_token:
        try:
            outlook_events = fetch_outlook_events(target_user, time_min, time_max)
            
            for item in outlook_events:
                start_time_str = item.get('start', {}).get('dateTime')
                end_time_str = item.get('end', {}).get('dateTime')
                
                # Graph API returns UTC usually if Prefer header is set, we just take the substring
                start = start_time_str[:19] + 'Z' if start_time_str else None
                end = end_time_str[:19] + 'Z' if end_time_str else None
                
                g_title = item.get('subject', 'Busy').strip().lower()
                g_date_str = start[:10] if start else ""
                
                if (g_title, g_date_str) in existing_event_keys:
                    continue

                schedule.append({
                    "source": "outlook_calendar",
                    "title": item.get('subject', 'Busy'),
                    "description": item.get('bodyPreview', ''),
                    "start": start,
                    "end": end,
                    "type": "outlook_event",
                    "status": "scheduled",
                    "read_only": True,
                    "hangout_link": item.get('webLink'),
                    "event_link": item.get('webLink'),
                    "location": item.get('location', {}).get('displayName'),
                })
        except Exception as e:
            print(f"Failed to fetch Outlook Calendar for user {target_user_id}: {e}")
            err_str = str(e).lower()
            if "invalid_grant" in err_str or "token" in err_str:
                target_user.outlook_access_token = None
                target_user.outlook_refresh_token = None
                target_user.outlook_token_expires_at = None
            schedule.append({
                "source": "outlook_error",
                "title": "Outlook Calendar sync failed",
                "description": str(e),
                "start": None, "end": None,
                "type": "outlook_error",
                "status": "error",
                "read_only": True,
                "error": str(e),
            })
"""

# Find where google block ends
if "target_user.outlook_access_token" not in content:
    content = content.replace('def get_sort_key(x):', outlook_sync_block + '\n    def get_sort_key(x):')

outlook_avail_block = """
    for uid, user in users_map.items():
        if user.outlook_access_token and user.outlook_refresh_token:
            try:
                outlook_events = fetch_outlook_events(user, time_min, time_max)
                for item in outlook_events:
                    if item.get('showAs') == 'free':
                        continue
                        
                    start_dt_str = item.get('start', {}).get('dateTime')
                    end_dt_str = item.get('end', {}).get('dateTime')
                    
                    if start_dt_str and end_dt_str:
                        # Convert to same timezone as google items
                        b_s_dt = datetime.fromisoformat(start_dt_str[:19] + '+00:00').astimezone(tz_kolkata)
                        b_e_dt = datetime.fromisoformat(end_dt_str[:19] + '+00:00').astimezone(tz_kolkata)
                        
                        b_s_str = b_s_dt.strftime('%H:%M')
                        b_e_str = b_e_dt.strftime('%H:%M')
                        
                        if start_time and end_time:
                            if not is_overlapping(start_time, end_time, b_s_str, b_e_str):
                                continue
                                
                        busy_blocks.append({
                            "user_id": uid,
                            "user_name": user.full_name,
                            "source": "outlook_calendar",
                            "title": item.get('subject', 'Busy'),
                            "start": b_s_str,
                            "end": b_e_str,
                        })
            except Exception as e:
                err_str = str(e).lower()
                if "invalid_grant" in err_str or "token" in err_str:
                    user.outlook_access_token = None
                    user.outlook_refresh_token = None
                    user.outlook_token_expires_at = None
"""

if "user.outlook_access_token" not in content:
    content = content.replace('return success_response(', outlook_avail_block + '\n    return success_response(')

with open('app/api/v1/endpoints/planner.py', 'w', encoding='utf-8') as f:
    f.write(content)
