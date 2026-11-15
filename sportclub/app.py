"""Спортклуб «БИЦУХА» — сайт продажи абонементов.

Запуск:  python app.py      (откроется на http://127.0.0.1:5000)
"""
import os
import secrets
import sqlite3
from datetime import date, datetime

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import logic
from admin import bp as admin_bp
from auth import (check_csrf, csrf_token, home_for, load_user, role_required,
                  safe_next)
from db import (INSTANCE_DIR, close_db, get_db, init_db, q_all, q_one, q_val, tx)

CLUB_NAME = "БИЦУХА"

app = Flask(__name__)


def _secret_key():
    """SECRET_KEY из окружения, иначе генерируется один раз и хранится в instance/."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    path = os.path.join(INSTANCE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 7,
)
app.teardown_appcontext(close_db)
app.register_blueprint(admin_bp)

# ----------------------------------------------------------------------------
# Подписи и фильтры для шаблонов
# ----------------------------------------------------------------------------
SUB_STATUS = {"pending": "Ожидает оплаты", "active": "Активен", "frozen": "Заморожен",
              "expired": "Истёк", "cancelled": "Отменён"}
PAY_STATUS = {"pending": "Не оплачен", "paid": "Оплачен", "refunded": "Возвращён"}
PAY_METHOD = {"card": "Картой онлайн", "cash": "На ресепшене"}
ROLE_NAME = {"admin": "Администратор", "trainer": "Тренер", "client": "Клиент"}
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


@app.template_filter("date_ru")
def date_ru(value):
    if not value:
        return "—"
    return date.fromisoformat(value[:10]).strftime("%d.%m.%Y")


@app.template_filter("dt_ru")
def dt_ru(value):
    if not value:
        return "—"
    return datetime.strptime(value[:16], "%Y-%m-%d %H:%M").strftime("%d.%m.%Y %H:%M")


@app.template_filter("time_ru")
def time_ru(value):
    return value[11:16] if value else ""


@app.template_filter("day_title")
def day_title(value):
    d = date.fromisoformat(value[:10])
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"


@app.template_filter("phone")
def phone_fmt(value):
    v = value or ""
    if len(v) == 11:
        return f"+7 ({v[1:4]}) {v[4:7]}-{v[7:9]}-{v[9:]}"
    return v


@app.template_filter("rub")
def rub(value):
    return f"{int(value or 0):,}".replace(",", "\u00a0") + "\u00a0₽"


@app.template_filter("input_dt")
def input_dt(value):
    """'2026-11-10 18:30' -> '2026-11-10T18:30' для <input type=datetime-local>."""
    return value[:16].replace(" ", "T") if value else ""


@app.context_processor
def inject_globals():
    return dict(csrf_token=csrf_token, CLUB_NAME=CLUB_NAME, SUB_STATUS=SUB_STATUS,
                PAY_STATUS=PAY_STATUS, PAY_METHOD=PAY_METHOD, ROLE_NAME=ROLE_NAME,
                today_iso=date.today().isoformat())


@app.before_request
def before_request():
    logic.refresh_statuses()
    load_user()
    check_csrf()


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403,
                           text="У вас нет доступа к этой странице."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, text="Страница не найдена."), 404


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400,
                           text=getattr(e, "description", "Некорректный запрос.")), 400


# ----------------------------------------------------------------------------
# Публичные страницы
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template(
        "index.html",
        halls=q_all("SELECT * FROM halls ORDER BY id"),
        plans=q_all("SELECT * FROM plans WHERE is_active=1 ORDER BY duration_days"),
        classes=logic.upcoming_classes(days=7, limit=8),
        trainers=q_all(
            "SELECT e.* FROM employees e JOIN users u ON u.id=e.user_id "
            "WHERE e.position='trainer' AND u.is_active=1 ORDER BY e.id"),
        hall_count=q_val("SELECT COUNT(*) FROM halls"),
        has_about_photo=os.path.exists(os.path.join(app.static_folder, "img", "about.jpg")),
    )


@app.route("/apply", methods=["POST"])
def apply():
    """Форма «Пробное занятие»: данные посетителя сохраняются в таблицу applications."""
    if request.form.get("website"):               # скрытое поле-ловушка для ботов
        return redirect(url_for("index"))
    name = request.form.get("full_name", "").strip()
    phone = logic.normalize_phone(request.form.get("phone"))
    message = request.form.get("message", "").strip()[:500]
    if not logic.valid_name(name):
        flash("Укажите имя (от 2 символов).", "error")
    elif not phone:
        flash("Введите корректный номер телефона, например +7 999 123-45-67.", "error")
    else:
        dup = q_one("SELECT 1 FROM applications WHERE phone=? AND status='new' "
                    "AND created_at > datetime('now','localtime','-10 minutes')", (phone,))
        if not dup:
            get_db().execute(
                "INSERT INTO applications(full_name, phone, message) VALUES (?,?,?)",
                (name, phone, message or None))
        flash("Заявка принята! Администратор свяжется с вами в ближайшее время.", "success")
    return redirect(url_for("index") + "#contact")


@app.route("/schedule")
def schedule():
    classes = logic.upcoming_classes(days=14)
    days = {}
    for c in classes:
        days.setdefault(c["starts_at"][:10], []).append(c)
    my = set()
    problems = {}
    if g.user and g.user["role"] == "client":
        my = {r["class_id"] for r in q_all(
            "SELECT class_id FROM bookings WHERE client_id=? AND status='booked'",
            (g.user["client_id"],))}
        for c in classes:
            problems[c["id"]] = logic.booking_problem(g.user["client_id"], c)
    return render_template("schedule.html", days=days, my=my, problems=problems)


# ----------------------------------------------------------------------------
# Регистрация / вход / выход
# ----------------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for(home_for(g.user["role"])))
    nxt = safe_next(request.values.get("next"))
    form = {}
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in ("full_name", "phone", "email")}
        password, password2 = request.form.get("password", ""), request.form.get("password2", "")
        phone = logic.normalize_phone(form["phone"])
        email = form["email"].lower()
        error = None
        if not logic.valid_name(form["full_name"]):
            error = "Укажите ФИО (от 2 символов)."
        elif not phone:
            error = "Введите корректный номер телефона."
        elif not logic.valid_email(email):
            error = "Введите корректный e-mail."
        elif logic.check_password(password):
            error = logic.check_password(password)
        elif password != password2:
            error = "Пароли не совпадают."
        elif q_one("SELECT 1 FROM users WHERE email=?", (email,)):
            error = "Пользователь с таким e-mail уже зарегистрирован."
        elif q_one("SELECT 1 FROM clients WHERE phone=?", (phone,)):
            error = "Клиент с таким телефоном уже зарегистрирован."
        if error:
            flash(error, "error")
        else:
            conn = get_db()
            try:
                with tx(conn):
                    uid = conn.execute(
                        "INSERT INTO users(email, password_hash, role) VALUES (?,?, 'client')",
                        (email, generate_password_hash(password))).lastrowid
                    conn.execute("INSERT INTO clients(user_id, full_name, phone) VALUES (?,?,?)",
                                 (uid, form["full_name"], phone))
            except sqlite3.IntegrityError:
                flash("Не удалось создать аккаунт: такие данные уже есть в системе.", "error")
            else:
                session.clear()
                session["uid"] = uid
                flash("Регистрация прошла успешно. Добро пожаловать в клуб!", "success")
                return redirect(nxt or url_for("cabinet"))
    return render_template("register.html", form=form, next=nxt)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for(home_for(g.user["role"])))
    nxt = safe_next(request.values.get("next"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = q_one("SELECT * FROM users WHERE email=?", (email,))
        if user and user["is_active"] and check_password_hash(user["password_hash"],
                                                              request.form.get("password", "")):
            session.clear()
            session["uid"] = user["id"]
            session.permanent = bool(request.form.get("remember"))
            if user["role"] == "client" and nxt:
                return redirect(nxt)
            return redirect(url_for(home_for(user["role"])))
        flash("Неверный e-mail или пароль.", "error")
    return render_template("login.html", next=nxt)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("index"))


# ----------------------------------------------------------------------------
# Покупка абонемента
# ----------------------------------------------------------------------------
@app.route("/buy/<int:plan_id>", methods=["GET", "POST"])
@role_required("client")
def buy(plan_id):
    plan = q_one("SELECT * FROM plans WHERE id=? AND is_active=1", (plan_id,))
    if plan is None:
        abort(404)
    existing = logic.live_subscription(g.user["client_id"])
    if existing:
        flash("У вас уже есть абонемент. Новый можно оформить после его окончания.", "error")
        return redirect(url_for("cabinet"))

    if request.method == "POST":
        method = request.form.get("method")
        if method not in ("card", "cash"):
            flash("Выберите способ оплаты.", "error")
            return redirect(url_for("buy", plan_id=plan_id))
        conn = get_db()
        try:
            with tx(conn):
                if logic.live_subscription(g.user["client_id"]):
                    raise sqlite3.IntegrityError("live subscription exists")
                # Онлайн-оплата картой — демо-режим: платёж считается проведённым сразу.
                logic.create_subscription(conn, g.user["client_id"], plan, method,
                                          paid=(method == "card"))
        except sqlite3.IntegrityError:
            flash("У вас уже есть неоконченный абонемент.", "error")
            return redirect(url_for("cabinet"))
        if method == "card":
            flash(f"Оплата прошла успешно. Абонемент «{plan['name']}» активирован!", "success")
        else:
            flash("Абонемент оформлен. Оплатите его на ресепшене — "
                  "после подтверждения администратором он станет активным.", "success")
        return redirect(url_for("cabinet"))
    return render_template("checkout.html", plan=plan)


# ----------------------------------------------------------------------------
# Личный кабинет клиента
# ----------------------------------------------------------------------------
@app.route("/cabinet")
@role_required("client")
def cabinet():
    cid = g.user["client_id"]
    client = q_one("SELECT * FROM clients WHERE id=?", (cid,))
    history = q_all(
        "SELECT s.*, p.name AS plan_name, pay.amount, pay.status AS pay_status, pay.method "
        "FROM subscriptions s JOIN plans p ON p.id=s.plan_id "
        "LEFT JOIN payments pay ON pay.subscription_id=s.id "
        "WHERE s.client_id=? ORDER BY s.id DESC", (cid,))
    bookings = q_all(
        "SELECT b.id AS booking_id, b.status AS booking_status, c.*, "
        "       e.full_name AS trainer_name, h.name AS hall_name "
        "FROM bookings b JOIN classes c ON c.id=b.class_id "
        "JOIN employees e ON e.id=c.trainer_id JOIN halls h ON h.id=c.hall_id "
        "WHERE b.client_id=? AND b.status='booked' ORDER BY c.starts_at DESC", (cid,))
    now_s = logic.now().strftime(logic.DT_FMT)
    return render_template(
        "cabinet.html", client=client, sub=logic.live_subscription(cid), history=history,
        upcoming=[b for b in bookings if b["starts_at"] >= now_s][::-1],
        past=[b for b in bookings if b["starts_at"] < now_s][:10],
        now_s=now_s)


@app.route("/cabinet/profile", methods=["POST"])
@role_required("client")
def update_profile():
    name = request.form.get("full_name", "").strip()
    phone = logic.normalize_phone(request.form.get("phone"))
    if not logic.valid_name(name):
        flash("Укажите ФИО (от 2 символов).", "error")
    elif not phone:
        flash("Введите корректный номер телефона.", "error")
    elif q_one("SELECT 1 FROM clients WHERE phone=? AND id<>?", (phone, g.user["client_id"])):
        flash("Этот телефон уже привязан к другому клиенту.", "error")
    else:
        get_db().execute("UPDATE clients SET full_name=?, phone=? WHERE id=?",
                         (name, phone, g.user["client_id"]))
        flash("Профиль обновлён.", "success")
    return redirect(url_for("cabinet"))


@app.route("/cabinet/password", methods=["POST"])
@role_required("client", "trainer", "admin")
def change_password():
    user = q_one("SELECT * FROM users WHERE id=?", (g.user["id"],))
    new = request.form.get("new_password", "")
    if not check_password_hash(user["password_hash"], request.form.get("old_password", "")):
        flash("Текущий пароль указан неверно.", "error")
    elif logic.check_password(new):
        flash(logic.check_password(new), "error")
    elif new != request.form.get("new_password2", ""):
        flash("Новые пароли не совпадают.", "error")
    else:
        get_db().execute("UPDATE users SET password_hash=? WHERE id=?",
                         (generate_password_hash(new), user["id"]))
        flash("Пароль изменён.", "success")
    return redirect(url_for(home_for(g.user["role"])))


@app.route("/cabinet/subscription/<int:sub_id>/<action>", methods=["POST"])
@role_required("client")
def subscription_action(sub_id, action):
    conn = get_db()
    with tx(conn):
        sub = conn.execute("SELECT * FROM subscriptions WHERE id=? AND client_id=?",
                           (sub_id, g.user["client_id"])).fetchone()
        if sub is None:
            abort(404)
        if action == "freeze" and sub["status"] == "active":
            logic.freeze(conn, sub)
            flash("Абонемент заморожен. Будущие записи на занятия отменены.", "success")
        elif action == "unfreeze" and sub["status"] == "frozen":
            days = logic.unfreeze(conn, sub)
            flash(f"Абонемент разморожен. Срок продлён на {days} дн.", "success")
        elif action == "cancel" and sub["status"] == "pending":
            conn.execute("DELETE FROM payments WHERE subscription_id=? AND status='pending'", (sub_id,))
            conn.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sub_id,))
            flash("Заказ абонемента отменён.", "success")
        else:
            flash("Это действие сейчас недоступно.", "error")
    return redirect(url_for("cabinet"))


# ----------------------------------------------------------------------------
# Запись на занятия
# ----------------------------------------------------------------------------
@app.route("/book/<int:class_id>", methods=["POST"])
@role_required("client")
def book(class_id):
    conn = get_db()
    with tx(conn):                      # проверка мест и вставка — в одной транзакции
        cls = conn.execute(logic.CLASS_SELECT + "WHERE c.id=?", (class_id,)).fetchone()
        if cls is None:
            abort(404)
        problem = logic.booking_problem(g.user["client_id"], cls)
        if problem:
            flash(problem, "error")
        else:
            conn.execute(
                "INSERT INTO bookings(class_id, client_id) VALUES (?,?) "
                "ON CONFLICT(class_id, client_id) DO UPDATE SET status='booked', "
                "created_at=datetime('now','localtime')", (class_id, g.user["client_id"]))
            flash(f"Вы записаны: {cls['title']}, {dt_ru(cls['starts_at'])}.", "success")
    return redirect(request.form.get("back") if safe_next(request.form.get("back"))
                    else url_for("schedule"))


@app.route("/booking/<int:booking_id>/cancel", methods=["POST"])
@role_required("client")
def cancel_booking(booking_id):
    row = q_one(
        "SELECT b.id, c.starts_at FROM bookings b JOIN classes c ON c.id=b.class_id "
        "WHERE b.id=? AND b.client_id=? AND b.status='booked'",
        (booking_id, g.user["client_id"]))
    if row is None:
        abort(404)
    if row["starts_at"] <= logic.now().strftime(logic.DT_FMT):
        flash("Занятие уже началось — отменить запись нельзя.", "error")
    else:
        get_db().execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
        flash("Запись отменена.", "success")
    return redirect(request.form.get("back") if safe_next(request.form.get("back"))
                    else url_for("cabinet"))


# ----------------------------------------------------------------------------
# Кабинет тренера
# ----------------------------------------------------------------------------
@app.route("/trainer")
@role_required("trainer")
def trainer_page():
    classes = q_all(
        logic.CLASS_SELECT + "WHERE c.trainer_id=? AND c.starts_at >= ? ORDER BY c.starts_at",
        (g.user["employee_id"], logic.now().strftime(logic.DT_FMT)))
    attendees = {}
    for c in classes:
        attendees[c["id"]] = q_all(
            "SELECT cl.full_name, cl.phone FROM bookings b JOIN clients cl ON cl.id=b.client_id "
            "WHERE b.class_id=? AND b.status='booked' ORDER BY cl.full_name", (c["id"],))
    return render_template("trainer.html", classes=classes, attendees=attendees)


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
else:
    init_db()
