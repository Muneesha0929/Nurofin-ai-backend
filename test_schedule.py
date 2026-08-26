import asyncio
import urllib.request
import urllib.error

def test():
    try:
        url = "http://localhost:3000/api/v1/planner/schedule/3?start_date=2026-07-27&end_date=2026-09-25"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            print(response.read())
    except urllib.error.HTTPError as e:
        print("HTTP Error:", e.code)
        print("Response:", e.read().decode("utf-8"))
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test()
