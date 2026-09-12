from datetime import datetime

def parse_time(time_str: str) -> datetime.time:
    """Parses a time string in HH:MM format."""
    return datetime.strptime(time_str, "%H:%M").time()

def is_overlapping(start1: str, end1: str, start2: str, end2: str) -> bool:
    """
    Checks if two time intervals overlap.
    Standard exclusive-end condition: start1 < end2 AND end1 > start2.
    Assumes all inputs are "HH:MM" strings.
    """
    if not start1 or not end1 or not start2 or not end2:
        return False
        
    s1 = parse_time(start1)
    e1 = parse_time(end1)
    s2 = parse_time(start2)
    e2 = parse_time(end2)
    
    return s1 < e2 and e1 > s2

def format_conflict(conflict_type: str, item_id: int, source: str, title: str, start_time: str, end_time: str, mask_title: bool = False):
    return {
        "type": conflict_type,
        "id": item_id,
        "source": source,
        "title": "Busy" if mask_title else title,
        "start_time": start_time,
        "end_time": end_time
    }
