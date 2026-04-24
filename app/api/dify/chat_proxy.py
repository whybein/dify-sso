import logging

import requests as http_client
from flask import request, Response

from app.api.router import api, logger
from app.configs import config
from app.services.token import TokenService


_HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-length",  # requests handles this automatically
    # requests 가 자동으로 응답을 decompress 하므로 Content-Encoding 헤더를
    # 그대로 전달하면 브라우저가 한 번 더 풀려다 ERR_CONTENT_DECODING_FAILED 발생.
    "content-encoding",
])


def _proxy_with_embed_cookie(path: str):
    """Proxy request to Dify web frontend and inject embed origin cookie."""
    if not config.DIFY_WEB_INTERNAL_URL:
        logger.error("DIFY_WEB_INTERNAL_URL is not configured")
        return {"error": "proxy not configured"}, 502

    target = f"{config.DIFY_WEB_INTERNAL_URL.rstrip('/')}/{path}"
    if request.query_string:
        target += f"?{request.query_string.decode('utf-8', errors='replace')}"

    # Forward headers, stripping hop-by-hop and host
    forward_headers = {
        k: v for k, v in request.headers
        if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }

    try:
        upstream = http_client.get(
            target,
            headers=forward_headers,
            allow_redirects=False,
            stream=True,
            timeout=15,
        )
    except Exception as e:
        logger.exception("chat_embed_proxy upstream error: %s", e)
        return {"error": "upstream unavailable"}, 502

    # Copy response headers, stripping hop-by-hop
    response_headers = [
        (k, v) for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    ]

    response = Response(
        upstream.content,
        status=upstream.status_code,
        headers=response_headers,
    )

    # Detect iframe context and stamp embed origin cookie
    sec_fetch_dest = request.headers.get("Sec-Fetch-Dest", "")
    embed_origin_val = ""
    if sec_fetch_dest == "iframe":
        embed_origin_val = request.headers.get("Referer", "")
        logger.info("chat_embed_proxy: iframe detected, embed_origin=%s", embed_origin_val)

    is_secure = TokenService.is_secure()
    response.set_cookie(
        "dify_embed_origin",
        embed_origin_val,
        max_age=120,
        path="/",
        httponly=True,
        secure=is_secure,
        samesite="None" if is_secure else "Lax",
    )

    return response


@api.route("/chat/<path:subpath>", methods=["GET"])
def chat_embed_proxy(subpath: str):
    return _proxy_with_embed_cookie(f"chat/{subpath}")


@api.route("/chatbot/<path:subpath>", methods=["GET"])
def chatbot_embed_proxy(subpath: str):
    return _proxy_with_embed_cookie(f"chatbot/{subpath}")
