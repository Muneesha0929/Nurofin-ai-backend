import urllib.request
import urllib.parse
import json

data = urllib.parse.urlencode({'username': 'vincent@nurofin.com', 'password': 'password'}).encode()
req = urllib.request.Request('http://127.0.0.1:8000/api/v1/auth/login', data=data)
try:
    resp = urllib.request.urlopen(req)
    token = json.loads(resp.read())['data']['access_token']
except Exception as e:
    token = None

if token:
    target_data = json.dumps({
        "title": "Global Target",
        "month": "2023-10",
        "is_global": True,
        "user_id": 2
    }).encode()
    req = urllib.request.Request('http://127.0.0.1:8000/api/v1/targets/', data=target_data, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        resp = urllib.request.urlopen(req)
        print("Success:", resp.getcode())
    except urllib.error.HTTPError as e:
        print("Error HTTP:", e.code, e.read().decode())
else:
    print("No token")
