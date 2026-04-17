"""Current user info endpoints.

/api/me              — browser용 (JWT 인증)
/internal/user-email — Dify 워크플로우용 (API Key 인증, conversation_id 기반)
"""
import hmac

from flask import jsonify, request
from sqlalchemy import text

from app.api.router import api, logger
from app.configs import config
from app.extensions.ext_database import db
from app.models.account import Account
from app.services.auth_context import get_current_user_id


@api.get("/api/me")
def get_current_user_info():
    """브라우저에서 JWT 토큰으로 본인 정보 조회."""
    user_id = get_current_user_id(request)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    account = db.session.query(Account).filter(Account.id == user_id).first()
    if not account:
        return jsonify({"error": "Account not found"}), 404

    return jsonify({
        "email": account.email,
        "name": account.name,
    })


@api.get("/internal/user-email")
def get_user_email_internal():
    """Dify 워크플로우에서 conversation_id로 유저 이메일 조회."""
    api_key = request.headers.get("x-internal-key", "")
    if not api_key or not config.INTERNAL_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    if not hmac.compare_digest(api_key, config.INTERNAL_API_KEY):
        return jsonify({"error": "Unauthorized"}), 401

    conversation_id = request.args.get("conversation_id", "").strip()
    if not conversation_id:
        return jsonify({"error": "conversation_id parameter required"}), 400

    row = db.session.execute(
        text("""
            SELECT a.email
            FROM conversations c
            JOIN accounts a ON a.id = c.from_account_id
            WHERE c.id = :cid
        """),
        {"cid": conversation_id},
    ).fetchone()

    if not row:
        return jsonify({"error": "Account not found for this conversation"}), 404

    return jsonify({"email": row[0]})
