"""Авторизация (сессии), проверка ролей и CSRF-защита форм."""
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import abort, flash, g, redirect, request, session, url_for

from db import q_one


def load_user():
    """Кладёт текущего пользователя в g.user (или None)."""
    g.user = None
    uid = session.get("uid")
    if not uid:
        return
    user = q_one("SELECT id, email, role, is_active FROM users WHERE id = ?", (uid,))
    if not user or not user["is_active"]:
        session.clear()
        return
    user["name"], user["client_id"], user["employee_id"] = None, None, None
    if user["role"] == "client":
        c = q_one("SELECT id, full_name FROM clients WHERE user_id = ?", (uid,))
        if c:
            user["client_id"], user["name"] = c["id"], c["full_name"]
    else:
        e = q_one("SELECT id, full_name FROM employees WHERE user_id = ?", (uid,))
        if e:
            user["employee_id"], user["name"] = e["id"], e["full_name"]
    g.user = user


def role_required(*roles):
    """Доступ только для вошедших пользователей с одной из ролей."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if g.user is None:
                flash("Войдите в аккаунт, чтобы продолжить.", "error")
                nxt = request.path if request.method == "GET" else None
                return redirect(url_for("login", next=nxt))
            if roles and g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def safe_next(target):
    """Разрешаем редирект только на внутренние страницы сайта."""
    if not target:
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def home_for(role):
    return {"admin": "admin.dashboard", "trainer": "trainer_page"}.get(role, "cabinet")


# --- CSRF ------------------------------------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def check_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Сессия формы устарела. Обновите страницу и повторите.")
