import re

with open('app/models/__init__.py', 'r') as f:
    content = f.read()

content += "from .target_score import TargetScore\n"

with open('app/models/__init__.py', 'w') as f:
    f.write(content)
