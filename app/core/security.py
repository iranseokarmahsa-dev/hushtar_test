"""Constant-time verification for the deployment's single admin account."""

import hmac

from app.core.config import ADMIN_PASSWORD, ADMIN_USERNAME


def verify_admin_credentials(username: str, password: str) -> bool:
	if not ADMIN_USERNAME or not ADMIN_PASSWORD:
		return False
	return hmac.compare_digest(username, ADMIN_USERNAME) and hmac.compare_digest(password, ADMIN_PASSWORD)
