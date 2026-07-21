"""Generate a bcrypt hash for an admin password.

    python deploy/make_admin_hash.py "your-new-password"

Paste the printed hash into krw-watcher-public.bat as ADMIN_PASSWORD_HASH=...
"""
import sys
import bcrypt

if len(sys.argv) < 2:
    print('usage: python deploy/make_admin_hash.py "<password>"')
    raise SystemExit(1)

print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())
