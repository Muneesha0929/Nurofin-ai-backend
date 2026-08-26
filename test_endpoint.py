import urllib.request
import urllib.error

url = "http://localhost:8000/api/v1/planner/schedule/3?start_date=2026-07-27&end_date=2026-09-25"
try:
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer YOUR_TOKEN_IF_NEEDED'}) # wait, I don't have token
    with urllib.request.urlopen(req) as response:
        print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode('utf-8'))
except Exception as e:
    print(e)
