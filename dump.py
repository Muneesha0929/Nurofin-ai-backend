async def app(scope, receive, send):
    from app.core.config import settings
    print('UVICORN DB URL:', settings.SQLALCHEMY_DATABASE_URI)
    await send({'type': 'http.response.start', 'status': 200})
    await send({'type': 'http.response.body', 'body': b'ok'})
