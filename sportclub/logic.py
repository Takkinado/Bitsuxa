"""Бизнес-правила клуба. Все проверки целостности данных собраны здесь."""
import re
from datetime import date, datetime, timedelta

from db import q_all, q_one, q_val, get_db

DT_FMT = "%Y-%m-%d %H:%M"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ----------------------------------------------------------------------------
# Валидаторы
# ----------------------------------------------------------------------------
def normalize_phone(raw):
    """'+7 (999) 123-45-67' / '8999...' -> '79991234567'. None, если номер некорректен."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits[0] == "7":
        return digits
    return None


def valid_email(email):
    return bool(email) and len(email) <= 120 and bool(EMAIL_RE.match(email))


def valid_name(name):
    return bool(name) and 2 <= len(name) <= 100


def check_password(pw):
    if len(pw or "") < 6:
        return "Пароль должен быть не короче 6 символов."
    if len(pw) > 100:
        return "Пароль слишком длинный."
    return None


# ----------------------------------------------------------------------------
# Время
# ----------------------------------------------------------------------------
def now():
    return datetime.now().replace(second=0, microsecond=0)


def today():
    return date.today()


def parse_dt(s):
    return datetime.strptime(s[:16], DT_FMT)


def parse_form_dt(s):
    """'2026-11-10T18:30' (datetime-local) -> datetime или None."""
    try:
        return datetime.strptime((s or "").replace("T", " ")[:16], DT_FMT)
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# Абонементы
# ----------------------------------------------------------------------------
def refresh_statuses():
    """Истёкшие абонементы -> expired. Вызывается перед каждым запросом (дёшево)."""
    conn = get_db()
    # сначала дешёвый SELECT, чтобы не брать блокировку на запись без необходимости
    if conn.execute("SELECT 1 FROM subscriptions WHERE status='active' AND end_date < ? LIMIT 1",
                    (today().isoformat(),)).fetchone():
        conn.execute("UPDATE subscriptions SET status='expired' "
                     "WHERE status='active' AND end_date < ?", (today().isoformat(),))


def live_subscription(client_id):
    """Единственный «живой» абонемент клиента (pending/active/frozen) или None."""
    return q_one(
        "SELECT s.*, p.name AS plan_name, p.price AS plan_price, p.duration_days, "
        "       pay.method AS pay_method, pay.status AS pay_status, pay.id AS payment_id "
        "FROM subscriptions s "
        "JOIN plans p ON p.id = s.plan_id "
        "LEFT JOIN payments pay ON pay.subscription_id = s.id "
        "WHERE s.client_id = ? AND s.status IN ('pending','active','frozen')", (client_id,))


def create_subscription(conn, client_id, plan, method, paid):
    """Оформляет абонемент и платёж. paid=True — деньги получены сразу (карта / касса)."""
    sid = conn.execute(
        "INSERT INTO subscriptions(client_id, plan_id, status) VALUES (?,?, 'pending')",
        (client_id, plan["id"])).lastrowid
    conn.execute(
        "INSERT INTO payments(subscription_id, amount, method, status) VALUES (?,?,?, 'pending')",
        (sid, plan["price"], method))
    if paid:
        mark_paid(conn, sid)
    return sid


def mark_paid(conn, subscription_id):
    """Платёж получен -> абонемент активируется с сегодняшнего дня."""
    sub = conn.execute(
        "SELECT s.id, p.duration_days FROM subscriptions s JOIN plans p ON p.id = s.plan_id "
        "WHERE s.id = ?", (subscription_id,)).fetchone()
    start = today()
    end = start + timedelta(days=sub["duration_days"])
    conn.execute(
        "UPDATE payments SET status='paid', paid_at=? WHERE subscription_id=?",
        (now().strftime(DT_FMT), subscription_id))
    conn.execute(
        "UPDATE subscriptions SET status='active', start_date=?, end_date=? WHERE id=?",
        (start.isoformat(), end.isoformat(), subscription_id))


def cancel_future_bookings(conn, client_id):
    conn.execute(
        "UPDATE bookings SET status='cancelled' WHERE client_id=? AND status='booked' "
        "AND class_id IN (SELECT id FROM classes WHERE starts_at > ?)",
        (client_id, now().strftime(DT_FMT)))


def freeze(conn, sub):
    conn.execute("UPDATE subscriptions SET status='frozen', frozen_from=? WHERE id=?",
                 (today().isoformat(), sub["id"]))
    cancel_future_bookings(conn, sub["client_id"])


def unfreeze(conn, sub):
    """Дни заморозки добавляются к сроку действия."""
    days = (today() - date.fromisoformat(sub["frozen_from"])).days
    new_end = date.fromisoformat(sub["end_date"]) + timedelta(days=days)
    conn.execute(
        "UPDATE subscriptions SET status='active', frozen_from=NULL, end_date=? WHERE id=?",
        (new_end.isoformat(), sub["id"]))
    return days


# ----------------------------------------------------------------------------
# Занятия и записи
# ----------------------------------------------------------------------------
CLASS_SELECT = (
    "SELECT c.*, e.full_name AS trainer_name, h.name AS hall_name, "
    "  (SELECT COUNT(*) FROM bookings b WHERE b.class_id=c.id AND b.status='booked') AS booked "
    "FROM classes c "
    "JOIN employees e ON e.id = c.trainer_id "
    "JOIN halls h ON h.id = c.hall_id ")


def upcoming_classes(days=14, limit=None):
    sql = (CLASS_SELECT +
           "WHERE c.status='planned' AND c.starts_at >= ? AND c.starts_at < ? "
           "ORDER BY c.starts_at")
    params = [now().strftime(DT_FMT), (now() + timedelta(days=days)).strftime(DT_FMT)]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return q_all(sql, params)


def class_end(starts_at, duration):
    return parse_dt(starts_at) + timedelta(minutes=duration)


def class_conflicts(start, duration, trainer_id, hall_id, exclude_id=0):
    """Пересечения по времени у тренера и в зале. Возвращает список текстов ошибок."""
    errors = []
    end = start + timedelta(minutes=duration)
    base = ("SELECT 1 FROM classes WHERE status='planned' AND id <> ? AND {col} = ? "
            "AND datetime(starts_at) < datetime(?) "
            "AND datetime(starts_at, '+' || duration_min || ' minutes') > datetime(?)")
    args = (end.strftime(DT_FMT), start.strftime(DT_FMT))
    if q_one(base.format(col="trainer_id"), (exclude_id, trainer_id) + args):
        errors.append("У тренера в это время уже есть другое занятие.")
    if q_one(base.format(col="hall_id"), (exclude_id, hall_id) + args):
        errors.append("Этот зал в это время занят другим занятием.")
    return errors


def booking_problem(client_id, cls):
    """Почему клиент НЕ может записаться на занятие (текст) или None, если может."""
    if cls["status"] != "planned":
        return "Занятие отменено."
    start = parse_dt(cls["starts_at"])
    if start <= now():
        return "Занятие уже началось или прошло."
    already = q_one("SELECT 1 FROM bookings WHERE class_id=? AND client_id=? AND status='booked'",
                    (cls["id"], client_id))
    if already:
        return "Вы уже записаны на это занятие."

    sub = live_subscription(client_id)
    if sub is None:
        return "Для записи нужен абонемент. Оформите его в разделе «Абонементы»."
    if sub["status"] == "pending":
        return "Ваш абонемент ещё не оплачен."
    if sub["status"] == "frozen":
        return "Абонемент заморожен. Разморозьте его в личном кабинете."
    if not (sub["start_date"] <= start.date().isoformat() <= sub["end_date"]):
        return "Абонемент не действует на дату этого занятия."

    booked = q_val("SELECT COUNT(*) FROM bookings WHERE class_id=? AND status='booked'", (cls["id"],))
    if booked >= cls["max_places"]:
        return "Свободных мест нет."

    end = start + timedelta(minutes=cls["duration_min"])
    clash = q_one(
        "SELECT 1 FROM bookings b JOIN classes c ON c.id=b.class_id "
        "WHERE b.client_id=? AND b.status='booked' AND c.status='planned' AND c.id<>? "
        "AND datetime(c.starts_at) < datetime(?) "
        "AND datetime(c.starts_at, '+' || c.duration_min || ' minutes') > datetime(?)",
        (client_id, cls["id"], end.strftime(DT_FMT), start.strftime(DT_FMT)))
    if clash:
        return "У вас уже есть запись на другое занятие в это время."
    return None
