import re

with open('alembic/versions/0423890795a8_add_is_deleted_to_document.py', 'r') as f:
    content = f.read()

# First revert my bad replace logic if it was run again, or just reset to original from git
# I'll just restore the file using git checkout, then apply the fix properly.
