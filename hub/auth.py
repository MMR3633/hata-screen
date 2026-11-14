import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl

INIT_MAX_AGE = 86400
SESSION_TTL = 7 * 86400
PC_CODE_TTL = 600


def check_init_data(init_data, token, allowed, now=None, max_age=INIT_MAX_AGE):
    try:
        fields = dict(parse_qsl(init_data or "", strict_parsing=True))
    except ValueError:
        return None
    received = fields.pop("hash", "")
    if not received or "user" not in fields:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        age = (now or time.time()) - int(fields.get("auth_date", "0"))
        user = json.loads(fields["user"])
    except ValueError:
        return None
    if age > max_age or user.get("id") not in allowed:
        return None
    return {"id": user["id"], "first_name": user.get("first_name", "")}


def _session_key(token):
    return hmac.new(b"hata-session", token.encode(), hashlib.sha256).digest()


def _sig(token, body):
    raw = hmac.new(_session_key(token), body.encode(), hashlib.sha256).digest()[:18]
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_session(uid, token, now=None):
    body = f"{uid}.{int((now or time.time()) + SESSION_TTL)}"
    return f"{body}.{_sig(token, body)}"


def read_session(tok, token, allowed, now=None):
    try:
        uid, exp, sig = (tok or "").split(".")
        uid, exp = int(uid), int(exp)
    except ValueError:
        return None
    if not hmac.compare_digest(_sig(token, f"{uid}.{exp}"), sig):
        return None
    if exp < (now or time.time()) or uid not in allowed:
        return None
    return uid


def new_pc_code(con, uid, now=None):
    code = secrets.token_urlsafe(18)
    con.execute("INSERT INTO pc_codes (code, uid, created) VALUES (?, ?, ?)", (code, uid, now or time.time()))
    return code


def use_pc_code(con, code, now=None):
    row = con.execute("SELECT uid, created FROM pc_codes WHERE code = ?", (code,)).fetchone()
    if row is None:
        return None
    con.execute("DELETE FROM pc_codes WHERE code = ?", (code,))
    if (now or time.time()) - row["created"] > PC_CODE_TTL:
        return None
    return row["uid"]
