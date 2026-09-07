"""Bound actual request bytes before multipart parsing can spool unlimited input."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
            return await self.app(scope, receive, send)
        maximum = self.settings.max_upload_bytes + 65536 if scope['path'] in {
            '/api/upload', '/api/upload-claim-file'} else self.settings.max_query_chars * 12 + 65536
        headers = dict(scope['headers'])
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            return await JSONResponse({'detail': 'Invalid content length.'}, status_code=400)(scope, receive, send)
        if declared > maximum:
            return await JSONResponse({'detail': 'Request body exceeds configured maximum.'}, status_code=413)(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > maximum:
                    raise HTTPException(413, 'Request body exceeds configured maximum.')
            return message

        await self.app(scope, bounded_receive, send)
