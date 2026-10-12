from datetime import datetime, timezone, timedelta
import requests
from urllib.parse import urlencode
from app.core.config import settings

OUTLOOK_CLIENT_ID = getattr(settings, "OUTLOOK_CLIENT_ID", "YOUR_OUTLOOK_CLIENT_ID")
OUTLOOK_CLIENT_SECRET = getattr(settings, "OUTLOOK_CLIENT_SECRET", "YOUR_OUTLOOK_CLIENT_SECRET")

# Microsoft Graph API Scopes
SCOPES = ['offline_access', 'Calendars.Read']

def get_outlook_auth_url(redirect_uri: str) -> str:
    params = {
        "client_id": OUTLOOK_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "response_mode": "query"
    }
    return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urlencode(params)

def exchange_code_for_tokens(code: str, redirect_uri: str):
    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    data = {
        "client_id": OUTLOOK_CLIENT_ID,
        "client_secret": OUTLOOK_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
    }
    response = requests.post(token_url, data=data)
    if not response.ok:
        raise Exception(f"Failed to exchange Outlook token: {response.text}")
    
    token_data = response.json()
    
    expires_in = token_data.get("expires_in", 3600)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    
    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": expires_at
    }

def refresh_credentials(user):
    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    
    # Check if expired
    if user.outlook_token_expires_at:
        try:
            expires_at = datetime.fromisoformat(str(user.outlook_token_expires_at))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > datetime.now(timezone.utc):
                return user.outlook_access_token # still valid
        except ValueError:
            pass

    if not user.outlook_refresh_token:
        raise Exception("No refresh token available")
        
    data = {
        "client_id": OUTLOOK_CLIENT_ID,
        "client_secret": OUTLOOK_CLIENT_SECRET,
        "refresh_token": user.outlook_refresh_token,
        "grant_type": "refresh_token",
        "scope": " ".join(SCOPES),
    }
    response = requests.post(token_url, data=data)
    if not response.ok:
        raise Exception(f"Failed to refresh Outlook token: {response.text}")
        
    token_data = response.json()
    expires_in = token_data.get("expires_in", 3600)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    
    user.outlook_access_token = token_data["access_token"]
    if token_data.get("refresh_token"):
        user.outlook_refresh_token = token_data["refresh_token"]
    user.outlook_token_expires_at = expires_at
    
    return user.outlook_access_token


def fetch_calendar_events(user, time_min: datetime, time_max: datetime):
    access_token = refresh_credentials(user)
    
    # Microsoft Graph endpoint for calendar view
    # Note: Using calendarView expands recurring events automatically
    url = "https://graph.microsoft.com/v1.0/me/calendarview"
    
    params = {
        "startDateTime": time_min.isoformat(),
        "endDateTime": time_max.isoformat(),
        "$select": "subject,bodyPreview,start,end,location,webLink,showAs",
        "$top": 250,
        "$orderby": "start/dateTime"
    }
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Prefer": 'outlook.timezone="UTC"'
    }
    
    response = requests.get(url, params=params, headers=headers)
    if not response.ok:
        raise Exception(f"Failed to fetch Outlook events: {response.text}")
        
    return response.json().get("value", [])
