import requests
import sys

def main():
    print("Testing Login API...")
    try:
        response = requests.post(
            "http://127.0.0.1:8000/api/v1/auth/login",
            data={
                "username": "muneesha09@gmail.com",
                "password": "qwerty"
            }
        )
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    main()
