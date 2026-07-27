import sys
import os
from datetime import datetime, date

def test():
    try:
        schedule = [
            {"date": None},
            {"date": "2026-07-27"},
            {"start": "2026-07-27T10:00:00Z"},
            {"date": datetime.now()},
            {"date": date.today()}
        ]
        
        def get_sort_key(x):
            dt = x.get("start") or x.get("date")
            if isinstance(dt, datetime):
                return dt.isoformat()
            if hasattr(dt, 'isoformat'): # date object
                return dt.isoformat()
            return str(dt) if dt else ""
            
        schedule.sort(key=get_sort_key, reverse=False)
        print("SORT SUCCESS")
        print(schedule)
    except Exception as e:
        print("SORT ERROR:", e)

test()
