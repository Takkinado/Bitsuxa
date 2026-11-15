"""Панель администратора (/admin/...). Доступна только роли admin."""
import sqlite3
from datetime import timedelta

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   url_for)
from werkzeug.security import generate_password_hash

import logic
from auth import role_required
from db import get_db, q_all, q_one, q_val, tx

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@role_required("admin")
def _only_admin():
    """Единая проверка роли для всех страниц админки."""
    return None


@bp.context_processor
def _badges():
    """Счётчики для бокового меню админки."""
    return dict(
        badge_apps=q_val("SELECT COUNT(*) FROM applications WHERE status='new'"),
        badge_pay=q_val("SELECT COUNT(*) FROM subscriptions s JOIN payments p ON p.subscription_id=s.id "
                        "WHERE s.status='pending' AND p.status='pending'"))


def F(name, label, type="text", value="", **kw):
    """Описание поля для универсальной формы admin/form.html."""
    return dict(name=name, label=label, type=type, value=value, **kw)


def form_page(title, fields, back, submit="Сохранить", note=None):
    return render_template("admin/form.html", title=title, fields=fields, back=back,
                           submit=submit, note=note)


def s(name):
    return request.form.get(name, "").strip()


def to_int(name, default=None):
    try:
        return int(request.form.get(name, ""))
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------
# Дашборд
# ----------------------------------------------------------------------------
@bp.route("/")
def dashboard():
    month_start = logic.today().replace(day=1).isoformat()
    stats = dict(
        clients=q_val("SELECT COUNT(*) FROM clients c JOIN users u ON u.id=c.user_id WHERE u.is_active=1"),
        active=q_val("SELECT COUNT(*) FROM subscriptions WHERE status='active'"),
        frozen=q_val("SELECT COUNT(*) FROM subscriptions WHERE status='frozen'"),
        pending=q_val("SELECT COUNT(*) FROM payments p JOIN subscriptions s ON s.id=p.subscription_id "
                      "WHERE p.status='pending' AND s.status='pending'"),
        new_apps=q_val("SELECT COUNT(*) FROM applications WHERE status='new'"),
        classes=q_val("SELECT COUNT(*) FROM classes WHERE status='planned' AND starts_at >= ?",
                      (logic.now().strftime(logic.DT_FMT),)),
        revenue=q_val("SELECT SUM(amount) FROM payments WHERE status='paid'"),
        revenue_month=q_val("SELECT SUM(amount) FROM payments WHERE status='paid' AND paid_at >= ?",
                            (month_start,)),
    )
    pending = q_all(
        "SELECT s.id, c.full_name, c.phone, pl.name AS plan_name, p.amount "
        "FROM subscriptions s JOIN clients c ON c.id=s.client_id "
        "JOIN plans pl ON pl.id=s.plan_id JOIN payments p ON p.subscription_id=s.id "
        "WHERE s.status='pending' AND p.status='pending' ORDER BY s.id")
    apps = q_all("SELECT * FROM applications WHERE status='new' ORDER BY id DESC LIMIT 5")
    return render_template("admin/dashboard.html", stats=stats, pending=pending, apps=apps)


# ----------------------------------------------------------------------------
# Заявки с сайта
# ----------------------------------------------------------------------------
@bp.route("/applications")
def applications():
    rows = q_all("SELECT * FROM applications ORDER BY (status='new') DESC, id DESC")
    return render_template("admin/applications.html", rows=rows)


@bp.route("/applications/<int:app_id>/<action>", methods=["POST"])
def application_action(app_id, action):
    if not q_one("SELECT 1 FROM applications WHERE id=?", (app_id,)):
        abort(404)
    if action == "process":
        get_db().execute("UPDATE applications SET status='processed' WHERE id=?", (app_id,))
        flash("Заявка отмечена как обработанная.", "success")
    elif action == "delete":
        get_db().execute("DELETE FROM applications WHERE id=?", (app_id,))
        flash("Заявка удалена.", "success")
    else:
        abort(404)
    return redirect(request.form.get("back") or url_for("admin.applications"))


# ----------------------------------------------------------------------------
# Клиенты
# ----------------------------------------------------------------------------
@bp.route("/clients")
def clients():
    term = request.args.get("q", "").strip()
    sql = ("SELECT c.*, u.email, u.is_active, "
           "  (SELECT status FROM subscriptions s WHERE s.client_id=c.id "
           "   AND s.status IN ('pending','active','frozen')) AS sub_status, "
           "  (SELECT end_date FROM subscriptions s WHERE s.client_id=c.id "
           "   AND s.status IN ('pending','active','frozen')) AS sub_end "
           "FROM clients c JOIN users u ON u.id=c.user_id ")
    params = ()
    if term:
        sql += "WHERE c.full_name LIKE ? OR c.phone LIKE ? OR u.email LIKE ? "
        like = f"%{term}%"
        params = (like, f"%{''.join(ch for ch in term if ch.isdigit()) or term}%", like)
    rows = q_all(sql + "ORDER BY c.id DESC", params)
    return render_template("admin/clients.html", rows=rows, term=term)


def _client_fields(v=None, new=False):
    v = v or {}
    fields = [
        F("full_name", "ФИО", value=v.get("full_name", ""), required=True),
        F("phone", "Телефон", "tel", v.get("phone", ""), required=True, placeholder="+7 999 123-45-67"),
    ]
    if new:
        fields += [F("email", "E-mail (логин)", "email", v.get("email", ""), required=True),
                   F("password", "Пароль", "password", "", required=True,
                     help="Не короче 6 символов. Сообщите его клиенту.")]
    return fields


@bp.route("/clients/new", methods=["GET", "POST"])
def client_new():
    v = {}
    if request.method == "POST":
        v = {k: s(k) for k in ("full_name", "phone", "email")}
        phone, email, pw = logic.normalize_phone(v["phone"]), v["email"].lower(), request.form.get("password", "")
        err = None
        if not logic.valid_name(v["full_name"]):
            err = "Укажите ФИО."
        elif not phone:
            err = "Некорректный номер телефона."
        elif not logic.valid_email(email):
            err = "Некорректный e-mail."
        elif logic.check_password(pw):
            err = logic.check_password(pw)
        elif q_one("SELECT 1 FROM users WHERE email=?", (email,)):
            err = "Пользователь с таким e-mail уже существует."
        elif q_one("SELECT 1 FROM clients WHERE phone=?", (phone,)):
            err = "Клиент с таким телефоном уже существует."
        if err:
            flash(err, "error")
        else:
            conn = get_db()
            with tx(conn):
                uid = conn.execute("INSERT INTO users(email,password_hash,role) VALUES (?,?, 'client')",
                                   (email, generate_password_hash(pw))).lastrowid
                cid = conn.execute("INSERT INTO clients(user_id,full_name,phone) VALUES (?,?,?)",
                                   (uid, v["full_name"], phone)).lastrowid
            flash("Клиент создан.", "success")
            return redirect(url_for("admin.client_detail", client_id=cid))
    return form_page("Новый клиент", _client_fields(v, new=True), url_for("admin.clients"), "Создать")


@bp.route("/clients/<int:client_id>")
def client_detail(client_id):
    client = q_one("SELECT c.*, u.email, u.is_active FROM clients c JOIN users u ON u.id=c.user_id "
                   "WHERE c.id=?", (client_id,))
    if client is None:
        abort(404)
    history = q_all(
        "SELECT s.*, p.name AS plan_name, pay.amount, pay.status AS pay_status, pay.method "
        "FROM subscriptions s JOIN plans p ON p.id=s.plan_id "
        "LEFT JOIN payments pay ON pay.subscription_id=s.id WHERE s.client_id=? ORDER BY s.id DESC",
        (client_id,))
    bookings = q_all(
        "SELECT c.title, c.starts_at, c.status AS class_status, h.name AS hall_name, b.status "
        "FROM bookings b JOIN classes c ON c.id=b.class_id JOIN halls h ON h.id=c.hall_id "
        "WHERE b.client_id=? ORDER BY c.starts_at DESC LIMIT 15", (client_id,))
    return render_template(
        "admin/client_detail.html", client=client, history=history, bookings=bookings,
        live=logic.live_subscription(client_id),
        plans=q_all("SELECT * FROM plans WHERE is_active=1 ORDER BY duration_days"))


@bp.route("/clients/<int:client_id>/edit", methods=["GET", "POST"])
def client_edit(client_id):
    client = q_one("SELECT * FROM clients WHERE id=?", (client_id,))
    if client is None:
        abort(404)
    v = dict(client)
    if request.method == "POST":
        name, phone = s("full_name"), logic.normalize_phone(s("phone"))
        v.update(full_name=name, phone=s("phone"))
        if not logic.valid_name(name):
            flash("Укажите ФИО.", "error")
        elif not phone:
            flash("Некорректный номер телефона.", "error")
        elif q_one("SELECT 1 FROM clients WHERE phone=? AND id<>?", (phone, client_id)):
            flash("Этот телефон уже привязан к другому клиенту.", "error")
        else:
            get_db().execute("UPDATE clients SET full_name=?, phone=? WHERE id=?", (name, phone, client_id))
            flash("Данные клиента сохранены.", "success")
            return redirect(url_for("admin.client_detail", client_id=client_id))
    return form_page("Редактирование клиента", _client_fields(v),
                     url_for("admin.client_detail", client_id=client_id))


@bp.route("/clients/<int:client_id>/account", methods=["POST"])
def client_account(client_id):
    """Блокировка/разблокировка аккаунта и сброс пароля."""
    client = q_one("SELECT * FROM clients WHERE id=?", (client_id,))
    if client is None:
        abort(404)
    action = request.form.get("action")
    if action == "toggle":
        row = q_one("SELECT is_active FROM users WHERE id=?", (client["user_id"],))
        get_db().execute("UPDATE users SET is_active=? WHERE id=?", (0 if row["is_active"] else 1, client["user_id"]))
        flash("Аккаунт заблокирован." if row["is_active"] else "Аккаунт разблокирован.", "success")
    elif action == "password":
        pw = request.form.get("new_password", "")
        if logic.check_password(pw):
            flash(logic.check_password(pw), "error")
        else:
            get_db().execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(pw), client["user_id"]))
            flash("Пароль клиента изменён.", "success")
    return redirect(url_for("admin.client_detail", client_id=client_id))


@bp.route("/clients/<int:client_id>/sell", methods=["POST"])
def client_sell(client_id):
    """Продажа абонемента на ресепшене: оплата принята -> сразу активен."""
    plan = q_one("SELECT * FROM plans WHERE id=? AND is_active=1", (to_int("plan_id", 0),))
    if q_one("SELECT 1 FROM clients WHERE id=?", (client_id,)) is None:
        abort(404)
    if plan is None:
        flash("Выберите тариф.", "error")
    else:
        conn = get_db()
        try:
            with tx(conn):
                if logic.live_subscription(client_id):
                    raise sqlite3.IntegrityError
                logic.create_subscription(conn, client_id, plan, "cash", paid=True)
            flash(f"Абонемент «{plan['name']}» оформлен и активирован.", "success")
        except sqlite3.IntegrityError:
            flash("У клиента уже есть действующий или неоплаченный абонемент.", "error")
    return redirect(url_for("admin.client_detail", client_id=client_id))


# ----------------------------------------------------------------------------
# Абонементы
# ----------------------------------------------------------------------------
@bp.route("/subscriptions")
def subscriptions():
    status = request.args.get("status", "")
    sql = ("SELECT s.*, c.full_name, c.phone, p.name AS plan_name, "
           "       pay.amount, pay.status AS pay_status, pay.method "
           "FROM subscriptions s JOIN clients c ON c.id=s.client_id JOIN plans p ON p.id=s.plan_id "
           "LEFT JOIN payments pay ON pay.subscription_id=s.id ")
    params = ()
    if status in ("pending", "active", "frozen", "expired", "cancelled"):
        sql += "WHERE s.status=? "
        params = (status,)
    rows = q_all(sql + "ORDER BY s.id DESC", params)
    return render_template("admin/subscriptions.html", rows=rows, status=status)


@bp.route("/subscriptions/<int:sub_id>/<action>", methods=["POST"])
def subscription_action(sub_id, action):
    conn = get_db()
    with tx(conn):
        sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
        if sub is None:
            abort(404)
        if action == "confirm" and sub["status"] == "pending":
            logic.mark_paid(conn, sub_id)
            flash("Оплата подтверждена, абонемент активирован.", "success")
        elif action == "cancel" and sub["status"] in ("pending", "active", "frozen"):
            conn.execute("UPDATE subscriptions SET status='cancelled', frozen_from=NULL WHERE id=?", (sub_id,))
            pay = conn.execute("SELECT status FROM payments WHERE subscription_id=?", (sub_id,)).fetchone()
            if pay and pay["status"] == "paid":
                conn.execute("UPDATE payments SET status='refunded' WHERE subscription_id=?", (sub_id,))
            elif pay:
                conn.execute("DELETE FROM payments WHERE subscription_id=?", (sub_id,))
            logic.cancel_future_bookings(conn, sub["client_id"])
            flash("Абонемент отменён (оплата, если была, помечена как возвращённая).", "success")
        else:
            flash("Это действие недоступно для текущего статуса абонемента.", "error")
    return redirect(request.form.get("back") or url_for("admin.subscriptions"))


# ----------------------------------------------------------------------------
# Тарифы
# ----------------------------------------------------------------------------
@bp.route("/plans")
def plans():
    rows = q_all("SELECT p.*, (SELECT COUNT(*) FROM subscriptions s WHERE s.plan_id=p.id) AS used "
                 "FROM plans p ORDER BY p.duration_days")
    return render_template("admin/plans.html", rows=rows)


def _plan_fields(v):
    return [F("name", "Название", value=v.get("name", ""), required=True),
            F("duration_days", "Срок действия, дней", "number", v.get("duration_days", ""), required=True, min=1),
            F("price", "Цена, ₽", "number", v.get("price", ""), required=True, min=0),
            F("description", "Описание", "textarea", v.get("description") or ""),
            F("is_active", "Продаётся на сайте", "checkbox", v.get("is_active", 1))]


def _save_plan(plan_id=None):
    name, days, price = s("name"), to_int("duration_days"), to_int("price")
    desc, active = s("description"), 1 if request.form.get("is_active") else 0
    if not name or len(name) > 60:
        return "Укажите название тарифа (до 60 символов)."
    if not days or days < 1 or days > 3650:
        return "Срок должен быть от 1 до 3650 дней."
    if price is None or price < 0:
        return "Цена не может быть отрицательной."
    try:
        if plan_id:
            get_db().execute("UPDATE plans SET name=?,duration_days=?,price=?,description=?,is_active=? WHERE id=?",
                             (name, days, price, desc or None, active, plan_id))
        else:
            get_db().execute("INSERT INTO plans(name,duration_days,price,description,is_active) VALUES (?,?,?,?,?)",
                             (name, days, price, desc or None, active))
    except sqlite3.IntegrityError:
        return "Тариф с таким названием уже существует."
    return None


@bp.route("/plans/new", methods=["GET", "POST"])
def plan_new():
    v = {"is_active": 1}
    if request.method == "POST":
        v = dict(request.form, is_active=1 if request.form.get("is_active") else 0)
        err = _save_plan()
        if err:
            flash(err, "error")
        else:
            flash("Тариф добавлен.", "success")
            return redirect(url_for("admin.plans"))
    return form_page("Новый тариф", _plan_fields(v), url_for("admin.plans"), "Добавить")


@bp.route("/plans/<int:plan_id>/edit", methods=["GET", "POST"])
def plan_edit(plan_id):
    plan = q_one("SELECT * FROM plans WHERE id=?", (plan_id,))
    if plan is None:
        abort(404)
    v = dict(plan)
    if request.method == "POST":
        v = dict(request.form, is_active=1 if request.form.get("is_active") else 0)
        err = _save_plan(plan_id)
        if err:
            flash(err, "error")
        else:
            flash("Тариф сохранён. Уже проданные абонементы не изменились.", "success")
            return redirect(url_for("admin.plans"))
    return form_page("Редактирование тарифа", _plan_fields(v), url_for("admin.plans"),
                     note="Изменение цены и срока не затрагивает уже оформленные абонементы.")


@bp.route("/plans/<int:plan_id>/delete", methods=["POST"])
def plan_delete(plan_id):
    if q_val("SELECT COUNT(*) FROM subscriptions WHERE plan_id=?", (plan_id,)):
        flash("Тариф уже использовался в абонементах — его можно только скрыть с сайта.", "error")
    else:
        get_db().execute("DELETE FROM plans WHERE id=?", (plan_id,))
        flash("Тариф удалён.", "success")
    return redirect(url_for("admin.plans"))


# ----------------------------------------------------------------------------
# Залы
# ----------------------------------------------------------------------------
@bp.route("/halls")
def halls():
    rows = q_all("SELECT h.*, (SELECT COUNT(*) FROM classes c WHERE c.hall_id=h.id) AS used "
                 "FROM halls h ORDER BY h.id")
    return render_template("admin/halls.html", rows=rows)


def _hall_fields(v):
    return [F("name", "Название", value=v.get("name", ""), required=True),
            F("capacity", "Вместимость, чел.", "number", v.get("capacity", ""), required=True, min=1),
            F("description", "Описание", "textarea", v.get("description") or "")]


def _save_hall(hall_id=None):
    name, cap, desc = s("name"), to_int("capacity"), s("description")
    if not name or len(name) > 80:
        return "Укажите название зала."
    if not cap or cap < 1 or cap > 1000:
        return "Вместимость — число от 1 до 1000."
    try:
        if hall_id:
            if q_val("SELECT COUNT(*) FROM classes WHERE hall_id=? AND status='planned' AND max_places>?",
                     (hall_id, cap)):
                return "В расписании есть занятия с числом мест больше новой вместимости."
            get_db().execute("UPDATE halls SET name=?,capacity=?,description=? WHERE id=?",
                             (name, cap, desc or None, hall_id))
        else:
            get_db().execute("INSERT INTO halls(name,capacity,description) VALUES (?,?,?)",
                             (name, cap, desc or None))
    except sqlite3.IntegrityError:
        return "Зал с таким названием уже существует."
    return None


@bp.route("/halls/new", methods=["GET", "POST"])
def hall_new():
    v = {}
    if request.method == "POST":
        v = dict(request.form)
        err = _save_hall()
        if err:
            flash(err, "error")
        else:
            flash("Зал добавлен.", "success")
            return redirect(url_for("admin.halls"))
    return form_page("Новый зал", _hall_fields(v), url_for("admin.halls"), "Добавить")


@bp.route("/halls/<int:hall_id>/edit", methods=["GET", "POST"])
def hall_edit(hall_id):
    hall = q_one("SELECT * FROM halls WHERE id=?", (hall_id,))
    if hall is None:
        abort(404)
    v = dict(hall)
    if request.method == "POST":
        v = dict(request.form)
        err = _save_hall(hall_id)
        if err:
            flash(err, "error")
        else:
            flash("Зал сохранён.", "success")
            return redirect(url_for("admin.halls"))
    return form_page("Редактирование зала", _hall_fields(v), url_for("admin.halls"))


@bp.route("/halls/<int:hall_id>/delete", methods=["POST"])
def hall_delete(hall_id):
    if q_val("SELECT COUNT(*) FROM classes WHERE hall_id=?", (hall_id,)):
        flash("В этом зале есть занятия в расписании — удалить зал нельзя.", "error")
    else:
        get_db().execute("DELETE FROM halls WHERE id=?", (hall_id,))
        flash("Зал удалён.", "success")
    return redirect(url_for("admin.halls"))


# ----------------------------------------------------------------------------
# Тренеры
# ----------------------------------------------------------------------------
@bp.route("/trainers")
def trainers():
    rows = q_all(
        "SELECT e.*, u.email, u.is_active, "
        "  (SELECT COUNT(*) FROM classes c WHERE c.trainer_id=e.id AND c.status='planned' AND c.starts_at >= ?) AS future "
        "FROM employees e JOIN users u ON u.id=e.user_id WHERE e.position='trainer' ORDER BY e.id",
        (logic.now().strftime(logic.DT_FMT),))
    return render_template("admin/trainers.html", rows=rows)


def _trainer_fields(v, new=False):
    f = [F("full_name", "ФИО", value=v.get("full_name", ""), required=True),
         F("phone", "Телефон", "tel", v.get("phone", ""), required=True),
         F("specialization", "Специализация", value=v.get("specialization") or "", required=True,
           placeholder="Например: Бокс, ММА"),
         F("description", "О тренере (показывается на сайте)", "textarea", v.get("description") or "")]
    if new:
        f += [F("email", "E-mail (логин для входа)", "email", v.get("email", ""), required=True),
              F("password", "Пароль", "password", "", required=True)]
    else:
        f += [F("password", "Новый пароль (если нужно сменить)", "password", "")]
    return f


def _trainer_common():
    name, phone, spec = s("full_name"), logic.normalize_phone(s("phone")), s("specialization")
    if not logic.valid_name(name):
        return None, "Укажите ФИО."
    if not phone:
        return None, "Некорректный номер телефона."
    if not spec or len(spec) > 50:
        return None, "Укажите специализацию (до 50 символов)."
    return (name, phone, spec, s("description") or None), None


@bp.route("/trainers/new", methods=["GET", "POST"])
def trainer_new():
    v = {}
    if request.method == "POST":
        v = dict(request.form)
        data, err = _trainer_common()
        email, pw = s("email").lower(), request.form.get("password", "")
        if not err and not logic.valid_email(email):
            err = "Некорректный e-mail."
        if not err and logic.check_password(pw):
            err = logic.check_password(pw)
        if not err and q_one("SELECT 1 FROM users WHERE email=?", (email,)):
            err = "Пользователь с таким e-mail уже существует."
        if err:
            flash(err, "error")
        else:
            conn = get_db()
            with tx(conn):
                uid = conn.execute("INSERT INTO users(email,password_hash,role) VALUES (?,?, 'trainer')",
                                   (email, generate_password_hash(pw))).lastrowid
                conn.execute("INSERT INTO employees(user_id,full_name,phone,position,specialization,description) "
                             "VALUES (?,?,?, 'trainer', ?,?)", (uid, data[0], data[1], data[2], data[3]))
            flash("Тренер добавлен.", "success")
            return redirect(url_for("admin.trainers"))
    return form_page("Новый тренер", _trainer_fields(v, new=True), url_for("admin.trainers"), "Добавить")


@bp.route("/trainers/<int:emp_id>/edit", methods=["GET", "POST"])
def trainer_edit(emp_id):
    emp = q_one("SELECT * FROM employees WHERE id=? AND position='trainer'", (emp_id,))
    if emp is None:
        abort(404)
    v = dict(emp)
    if request.method == "POST":
        v = dict(request.form)
        data, err = _trainer_common()
        pw = request.form.get("password", "")
        if not err and pw and logic.check_password(pw):
            err = logic.check_password(pw)
        if err:
            flash(err, "error")
        else:
            conn = get_db()
            with tx(conn):
                conn.execute("UPDATE employees SET full_name=?,phone=?,specialization=?,description=? WHERE id=?",
                             (*data, emp_id))
                if pw:
                    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                                 (generate_password_hash(pw), emp["user_id"]))
            flash("Данные тренера сохранены.", "success")
            return redirect(url_for("admin.trainers"))
    return form_page("Редактирование тренера", _trainer_fields(v), url_for("admin.trainers"))


@bp.route("/trainers/<int:emp_id>/toggle", methods=["POST"])
def trainer_toggle(emp_id):
    emp = q_one("SELECT e.user_id, u.is_active FROM employees e JOIN users u ON u.id=e.user_id "
                "WHERE e.id=? AND e.position='trainer'", (emp_id,))
    if emp is None:
        abort(404)
    if emp["is_active"]:
        future = q_val("SELECT COUNT(*) FROM classes WHERE trainer_id=? AND status='planned' AND starts_at >= ?",
                       (emp_id, logic.now().strftime(logic.DT_FMT)))
        if future:
            flash(f"У тренера есть будущие занятия ({future}). Сначала отмените или переназначьте их.", "error")
            return redirect(url_for("admin.trainers"))
    get_db().execute("UPDATE users SET is_active=? WHERE id=?", (0 if emp["is_active"] else 1, emp["user_id"]))
    flash("Тренер скрыт с сайта и не может войти." if emp["is_active"] else "Тренер снова активен.", "success")
    return redirect(url_for("admin.trainers"))


# ----------------------------------------------------------------------------
# Расписание
# ----------------------------------------------------------------------------
@bp.route("/classes")
def classes():
    show = request.args.get("show", "upcoming")
    now_s = logic.now().strftime(logic.DT_FMT)
    if show == "all":
        rows = q_all(logic.CLASS_SELECT + "ORDER BY c.starts_at DESC LIMIT 200")
    else:
        rows = q_all(logic.CLASS_SELECT + "WHERE c.starts_at >= ? ORDER BY c.starts_at", (now_s,))
    return render_template("admin/classes.html", rows=rows, show=show, now_s=now_s)


def _class_fields(v):
    trainers = [(r["id"], f"{r['full_name']} ({r['specialization']})") for r in q_all(
        "SELECT e.* FROM employees e JOIN users u ON u.id=e.user_id "
        "WHERE e.position='trainer' AND u.is_active=1 ORDER BY e.full_name")]
    halls = [(r["id"], f"{r['name']} — до {r['capacity']} чел.") for r in q_all("SELECT * FROM halls ORDER BY name")]
    return [F("title", "Название занятия", value=v.get("title", ""), required=True),
            F("trainer_id", "Тренер", "select", v.get("trainer_id"), options=trainers, required=True),
            F("hall_id", "Зал", "select", v.get("hall_id"), options=halls, required=True),
            F("starts_at", "Дата и время начала", "datetime-local", input_dt_value(v.get("starts_at")), required=True),
            F("duration_min", "Длительность, мин", "number", v.get("duration_min", 60), required=True, min=15, step=5),
            F("max_places", "Количество мест", "number", v.get("max_places", ""), required=True, min=1)]


def input_dt_value(value):
    return (value or "")[:16].replace(" ", "T")


def _save_class(class_id=None):
    title, trainer_id, hall_id = s("title"), to_int("trainer_id"), to_int("hall_id")
    duration, places, start = to_int("duration_min"), to_int("max_places"), logic.parse_form_dt(s("starts_at"))
    if not title or len(title) > 100:
        return "Укажите название занятия."
    trainer = q_one("SELECT e.id FROM employees e JOIN users u ON u.id=e.user_id "
                    "WHERE e.id=? AND e.position='trainer' AND u.is_active=1", (trainer_id,))
    hall = q_one("SELECT * FROM halls WHERE id=?", (hall_id,))
    if trainer is None or hall is None:
        return "Выберите тренера и зал из списка."
    if start is None:
        return "Укажите корректные дату и время."
    if not duration or not 15 <= duration <= 480:
        return "Длительность — от 15 до 480 минут."
    if not places or places < 1:
        return "Количество мест должно быть положительным."
    if places > hall["capacity"]:
        return f"Мест ({places}) больше вместимости зала ({hall['capacity']})."
    if class_id is None and start < logic.now():
        return "Нельзя создать занятие в прошлом."
    if class_id:
        booked = q_val("SELECT COUNT(*) FROM bookings WHERE class_id=? AND status='booked'", (class_id,))
        if places < booked:
            return f"На занятие уже записано {booked} чел. — мест не может быть меньше."
    conflicts = logic.class_conflicts(start, duration, trainer_id, hall_id, class_id or 0)
    if conflicts:
        return " ".join(conflicts)
    stamp = start.strftime(logic.DT_FMT)
    if class_id:
        get_db().execute("UPDATE classes SET title=?,trainer_id=?,hall_id=?,starts_at=?,duration_min=?,max_places=? "
                         "WHERE id=?", (title, trainer_id, hall_id, stamp, duration, places, class_id))
    else:
        get_db().execute("INSERT INTO classes(title,trainer_id,hall_id,starts_at,duration_min,max_places) "
                         "VALUES (?,?,?,?,?,?)", (title, trainer_id, hall_id, stamp, duration, places))
    return None


@bp.route("/classes/new", methods=["GET", "POST"])
def class_new():
    v = {"duration_min": 60}
    if request.method == "POST":
        v = dict(request.form)
        v["starts_at"] = s("starts_at").replace("T", " ")
        err = _save_class()
        if err:
            flash(err, "error")
        else:
            flash("Занятие добавлено в расписание.", "success")
            return redirect(url_for("admin.classes"))
    return form_page("Новое занятие", _class_fields(v), url_for("admin.classes"), "Добавить")


@bp.route("/classes/<int:class_id>/edit", methods=["GET", "POST"])
def class_edit(class_id):
    cls = q_one("SELECT * FROM classes WHERE id=?", (class_id,))
    if cls is None:
        abort(404)
    v = dict(cls)
    if request.method == "POST":
        v = dict(request.form)
        v["starts_at"] = s("starts_at").replace("T", " ")
        err = _save_class(class_id)
        if err:
            flash(err, "error")
        else:
            flash("Занятие сохранено.", "success")
            return redirect(url_for("admin.class_detail", class_id=class_id))
    return form_page("Редактирование занятия", _class_fields(v),
                     url_for("admin.class_detail", class_id=class_id))


@bp.route("/classes/<int:class_id>")
def class_detail(class_id):
    cls = q_one(logic.CLASS_SELECT + "WHERE c.id=?", (class_id,))
    if cls is None:
        abort(404)
    attendees = q_all(
        "SELECT b.id AS booking_id, cl.id AS client_id, cl.full_name, cl.phone, b.created_at "
        "FROM bookings b JOIN clients cl ON cl.id=b.client_id "
        "WHERE b.class_id=? AND b.status='booked' ORDER BY b.created_at", (class_id,))
    return render_template("admin/class_detail.html", c=cls, attendees=attendees)


@bp.route("/classes/<int:class_id>/cancel", methods=["POST"])
def class_cancel(class_id):
    conn = get_db()
    with tx(conn):
        cls = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if cls is None:
            abort(404)
        conn.execute("UPDATE classes SET status='cancelled' WHERE id=?", (class_id,))
        n = conn.execute("UPDATE bookings SET status='cancelled' WHERE class_id=? AND status='booked'",
                         (class_id,)).rowcount
    flash(f"Занятие отменено. Отменено записей клиентов: {n}.", "success")
    return redirect(url_for("admin.classes"))


@bp.route("/classes/<int:class_id>/delete", methods=["POST"])
def class_delete(class_id):
    if q_val("SELECT COUNT(*) FROM bookings WHERE class_id=?", (class_id,)):
        flash("У занятия есть записи клиентов — удалить нельзя, отмените его.", "error")
    else:
        get_db().execute("DELETE FROM classes WHERE id=?", (class_id,))
        flash("Занятие удалено.", "success")
    return redirect(url_for("admin.classes"))


@bp.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
def booking_cancel(booking_id):
    row = q_one("SELECT class_id FROM bookings WHERE id=?", (booking_id,))
    if row is None:
        abort(404)
    get_db().execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
    flash("Запись клиента отменена.", "success")
    return redirect(url_for("admin.class_detail", class_id=row["class_id"]))
