import re

with open('app/api/v1/endpoints/planner.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('"google_connected": bool(target_user.google_access_token),', '"google_connected": bool(target_user.google_access_token),\n                "outlook_connected": bool(target_user.outlook_access_token),')
content = content.replace('"google_connected": bool(u.google_access_token),', '"google_connected": bool(u.google_access_token),\n                "outlook_connected": bool(u.outlook_access_token),')

# Now let's inject Outlook endpoints below Google ones
outlook_endpoints = """

@router.get("/outlook/login", response_model=APIResponse)
async def login_outlook(
    redirect_uri: str = Query(default=None),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if not redirect_uri:
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        redirect_uri = f"{frontend_url}/planner/outlook/callback"
    auth_url = get_outlook_auth_url(redirect_uri)
    return success_response(data={"auth_url": auth_url}, message="Outlook Auth URL generated")


@router.post("/outlook/callback", response_model=APIResponse)
async def outlook_callback(
    code: str = Query(...),
    redirect_uri: str = Query(default=None),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if not redirect_uri:
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        redirect_uri = f"{frontend_url}/planner/outlook/callback"
    try:
        tokens = exchange_outlook_tokens(code, redirect_uri)
        current_user.outlook_access_token = tokens["access_token"]
        current_user.outlook_refresh_token = tokens["refresh_token"]
        current_user.outlook_token_expires_at = tokens["expires_at"]

        await db.commit()
        await db.refresh(current_user)
        return success_response(data=None, message="Outlook Calendar connected successfully")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"Failed to connect Outlook Calendar: {str(e)}")


@router.post("/outlook/disconnect", response_model=APIResponse)
async def disconnect_outlook(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    current_user.outlook_access_token = None
    current_user.outlook_refresh_token = None
    current_user.outlook_token_expires_at = None
    await db.commit()
    return success_response(data=None, message="Outlook Calendar disconnected")
"""

if "/outlook/login" not in content:
    content = content.replace('@router.get("/schedule/{target_user_id}", response_model=APIResponse)', outlook_endpoints + '\n@router.get("/schedule/{target_user_id}", response_model=APIResponse)')

with open('app/api/v1/endpoints/planner.py', 'w', encoding='utf-8') as f:
    f.write(content)
