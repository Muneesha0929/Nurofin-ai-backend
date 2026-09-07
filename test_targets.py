import urllib.request
import urllib.parse
import json
import sys

# Try multiple users
users = ['ceo@nurofin.com', 'admin@nurofin.com', 'user@nurofin.com']
token = None
for u in users:
    data = urllib.parse.urlencode({'username': u, 'password': 'password'}).encode()
    req = urllib.request.Request('http://127.0.0.1:8000/api/v1/auth/login', data=data)
    try:
        resp = urllib.request.urlopen(req)
        token = json.loads(resp.read())['data']['access_token']
        print(f"Logged in as {u}")
        break
    except Exception:
        pass

if not token:
    print("Could not login to any mock account.")
    sys.exit(0)

req = urllib.request.Request('http://127.0.0.1:8000/api/v1/targets/my', headers={'Authorization': 'Bearer ' + token})
try:
    resp = urllib.request.urlopen(req)
    print(resp.read().decode())
except urllib.error.HTTPError as e:
    print("Error:", e.code, e.read().decode())
