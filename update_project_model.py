import re
import os

path = "c:/Users/Muneesha/Desktop/Nurofin Executive AI/Nurofin-ai-backend/app/models/project.py"
with open(path, "r") as f:
    txt = f.read()

txt = txt.replace('tasks = relationship("Task", back_populates="project")',
                  'tasks = relationship("Task", back_populates="project", primaryjoin="and_(Project.id==Task.project_id, Task.is_deleted==False)")')

with open(path, "w") as f:
    f.write(txt)
