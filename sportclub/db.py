"""Работа с базой данных SQLite: подключение, транзакции, создание схемы, демо-данные."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from flask import g
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.environ.get("SPORTCLUB_DB", os.path.join(INSTANCE_DIR, "sportclub.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None          # транзакциями управляем сами (см. tx)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def tx(conn=None):
    """Атомарная транзакция. BEGIN IMMEDIATE защищает от гонок (например, за последнее место)."""
    conn = conn or get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def q_all(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def q_one(sql, params=()):
    return get_db().execute(sql, params).fetchone()


def q_val(sql, params=(), default=0):
    row = get_db().execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else default


# ----------------------------------------------------------------------------
# Инициализация
# ----------------------------------------------------------------------------
def init_db(reset=False):
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = _connect()
    conn.execute("PRAGMA journal_mode = WAL")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    empty = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    if empty:
        seed(conn)
    conn.close()


def seed(conn):
    """Стартовые данные: админ, тренеры, залы, тарифы, расписание и один демо-клиент."""
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@sportclub.local")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "admin123")

    def add_user(email, password, role):
        cur = conn.execute(
            "INSERT INTO users(email, password_hash, role) VALUES (?,?,?)",
            (email, generate_password_hash(password), role),
        )
        return cur.lastrowid

    conn.execute("BEGIN")
    # --- сотрудники
    uid = add_user(admin_email, admin_pass, "admin")
    conn.execute(
        "INSERT INTO employees(user_id, full_name, phone, position) VALUES (?,?,?,'admin')",
        (uid, "Администратор клуба", "79990000000"),
    )
    trainers = [
        ("trainer1@sportclub.local", "Михаил Скалин", "79373072720", "Бокс, ММА",
         "Мастер спорта по боксу. Поставит удар, научит защите и выносливости. Опыт работы с бойцами ММА более 8 лет."),
        ("trainer2@sportclub.local", "Виктория Киль", "79129609540", "Йога, пилатес",
         "Инструктор йоги и пилатеса. Эксперт по биомеханике и восстановлению позвоночника, поможет с гибкостью и осанкой."),
    ]
    trainer_ids = []
    for email, name, phone, spec, desc in trainers:
        uid = add_user(email, "trainer123", "trainer")
        cur = conn.execute(
            "INSERT INTO employees(user_id, full_name, phone, position, specialization, description)"
            " VALUES (?,?,?,'trainer',?,?)", (uid, name, phone, spec, desc))
        trainer_ids.append(cur.lastrowid)

    # --- залы
    halls = [
        ("Зона свободных весов", "Гантельный ряд от 2 до 60 кг, силовые рамы, помосты для тяги.", 40),
        ("Бойцовская зона", "Профессиональный ринг, татами для борьбы, линейка тяжёлых мешков.", 20),
        ("Зал баланса и кора", "Студия с отличной звукоизоляцией для йоги и пилатеса.", 15),
    ]
    hall_ids = [conn.execute("INSERT INTO halls(name, description, capacity) VALUES (?,?,?)", h).lastrowid
                for h in halls]

    # --- тарифы (цены демонстрационные — меняются в админке)
    plans = [
        ("Месяц", 30, 2500, "Безлимитное посещение клуба и групповых занятий в течение 30 дней."),
        ("3 месяца", 90, 6500, "Выгоднее на 13% по сравнению с помесячной оплатой."),
        ("6 месяцев", 180, 11000, "Полгода тренировок без ограничений. Возможна заморозка."),
        ("Год", 365, 18000, "Максимальная выгода: экономия 12 000 ₽ по сравнению с помесячной оплатой."),
    ]
    for p in plans:
        conn.execute("INSERT INTO plans(name, duration_days, price, description) VALUES (?,?,?,?)", p)

    # --- расписание на ближайшие 14 дней
    template = [  # (время, название, тренер, зал, мест, мин)
        ("09:00", "Силовой пилатес", 1, 2, 12, 60),
        ("12:00", "Хатха-йога", 1, 2, 12, 75),
        ("18:30", "Бокс: спарринги", 0, 1, 14, 90),
        ("20:00", "ММА для новичков", 0, 1, 14, 90),
    ]
    today = datetime.now().date()
    for d in range(0, 14):
        day = today + timedelta(days=d)
        if day.weekday() == 6:       # воскресенье — выходной в расписании групповых
            continue
        for t, title, tr, hl, places, mins in template:
            conn.execute(
                "INSERT INTO classes(title, trainer_id, hall_id, starts_at, duration_min, max_places)"
                " VALUES (?,?,?,?,?,?)",
                (title, trainer_ids[tr], hall_ids[hl], f"{day.isoformat()} {t}", mins, places))

    # --- демо-клиент с активным абонементом
    uid = add_user("client@example.com", "client123", "client")
    cid = conn.execute(
        "INSERT INTO clients(user_id, full_name, phone) VALUES (?,?,?)",
        (uid, "Иван Петров", "79001234567")).lastrowid
    start = today
    end = start + timedelta(days=30)
    sid = conn.execute(
        "INSERT INTO subscriptions(client_id, plan_id, start_date, end_date, status) VALUES (?,1,?,?,'active')",
        (cid, start.isoformat(), end.isoformat())).lastrowid
    conn.execute(
        "INSERT INTO payments(subscription_id, amount, method, status, paid_at) VALUES (?,?,?,?,?)",
        (sid, 2500, "card", "paid", datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.execute("COMMIT")
