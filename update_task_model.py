import re
import os

path = "c:/Users/Muneesha/Desktop/Nurofin Executive AI/Nurofin-ai-backend/app/models/task.py"
with open(path, "r") as f:
    txt = f.read()

txt = txt.replace('parent = relationship("Task", remote_side=[id], backref="subtasks")',
                  'parent = relationship("Task", remote_side=[id], back_populates="subtasks")\n    subtasks = relationship("Task", back_populates="parent", primaryjoin="and_(Task.id==remote(Task.parent_id), Task.is_deleted==False)")')

with open(path, "w") as f:
    f.write(txt)
