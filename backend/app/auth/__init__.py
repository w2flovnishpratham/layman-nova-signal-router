"""Authentication layer: Google OAuth login + cookie sessions.

Login is open to any verified Google account. ADMIN_EMAILS only affects whether
a user is flagged is_admin (which in turn gates /api/admin/*) — it never blocks
a normal login.
"""
