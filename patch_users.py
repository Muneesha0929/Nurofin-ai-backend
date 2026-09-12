import re

with open('app/api/v1/endpoints/users.py', 'r') as f:
    content = f.read()

# Replace meeting overlap
content = re.sub(
    r'or_\(\s*and_\(Meeting.start_time <= start_time, Meeting.end_time > start_time\),\s*and_\(Meeting.start_time < end_time, Meeting.end_time >= end_time\),\s*and_\(Meeting.start_time >= start_time, Meeting.end_time <= end_time\)\s*\)',
    r'and_(Meeting.start_time < end_time, Meeting.end_time > start_time)',
    content,
    flags=re.MULTILINE
)

# Replace task overlap
content = re.sub(
    r'or_\(\s*and_\(Task.scheduled_start_time <= start_time, Task.scheduled_end_time > start_time\),\s*and_\(Task.scheduled_start_time < end_time, Task.scheduled_end_time >= end_time\),\s*and_\(Task.scheduled_start_time >= start_time, Task.scheduled_end_time <= end_time\)\s*\)',
    r'and_(Task.scheduled_start_time < end_time, Task.scheduled_end_time > start_time)',
    content,
    flags=re.MULTILINE
)

with open('app/api/v1/endpoints/users.py', 'w') as f:
    f.write(content)
