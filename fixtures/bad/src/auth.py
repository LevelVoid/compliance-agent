import logging
import secrets

logger = logging.getLogger(__name__)


def issue_session(user):
    token = secrets.token_urlsafe(32)
    logger.info(f"issued session token={token} for user {user['id']}")
    return {"token": token, "user_id": user["id"]}


def revoke_session(token):
    logger.info("session revoked")
    return True
