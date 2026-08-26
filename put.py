import requests
# Need auth token for the backend? The UI uses a token.
# If I don't have a token, it will return 401. Let's print the error.
res = requests.put("http://127.0.0.1:8000/api/v1/workcenter/49?status=completed&actual_completion_date=2026-08-25")
print(res.status_code, res.text)
