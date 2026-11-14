

def response_policy(get_response):
    def middleware(request):
        response = get_response(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    return middleware
