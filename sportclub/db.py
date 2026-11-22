"""Слой доступа к данным.

Основная БД проекта — Microsoft Access (.accdb), подключение через pyodbc.
Для разработки и автотестов можно переключиться на SQLite: DB_BACKEND=sqlite.

ВАЖНО: весь SQL в проекте написан в «общем» диалекте, который понимает и Access, и SQLite:
  * соединения только в виде  FROM (a INNER JOIN b ON ..) INNER JOIN c ON ..   (с AS у алиасов);
  * без LIMIT, COALESCE, ||, CASE, ON CONFLICT и функций даты — даты считаются в Python;
  * даты и время хранятся текстом ISO ('2026-11-10', '2026-11-10 18:30').
"""
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

from flask import g
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.environ.get("DB_BACKEND", "access").lower()          # 'access' | 'sqlite'


def _find_access_file():
    """Путь к базе Access: переменная ACCESS_DB, иначе любой .accdb/.mdb в папке проекта или в data/."""
    env = os.environ.get("ACCESS_DB")
    if env:
        return env
    for folder in (BASE_DIR, os.path.join(BASE_DIR, "data")):
        if os.path.isdir(folder):
            files = sorted(f for f in os.listdir(folder)
                           if f.lower().endswith((".accdb", ".mdb")) and not f.startswith("~"))
            if files:
                return os.path.join(folder, files[0])
    return os.path.join(BASE_DIR, "data", "Спортклуб.accdb")


ACCESS_PATH = _find_access_file()
SQLITE_PATH = os.environ.get("SPORTCLUB_DB", os.path.join(BASE_DIR, "instance", "sportclub.db"))
SCHEMA_ACCESS = os.path.join(BASE_DIR, "schema_access.sql")
SCHEMA_SQLITE = os.environ.get("SQLITE_SCHEMA", os.path.join(BASE_DIR, "schema.sql"))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

try:
    import pyodbc
    IntegrityError = (sqlite3.IntegrityError, pyodbc.IntegrityError)
except ImportError:                       # pyodbc нужен только для режима Access
    pyodbc = None
    IntegrityError = (sqlite3.IntegrityError,)

_LOCK = threading.RLock()                 # один писатель за раз (Access не умеет BEGIN IMMEDIATE)

# Имена колонок, которые в Access могут конфликтовать с зарезервированными словами — берём в [скобки].
_RISKY = ("name", "description", "position", "role", "method", "status", "message", "title",
          "amount", "capacity", "email", "phone", "price")
_WORDS = "|".join(_RISKY)
_RISKY_RE = re.compile(r"(?<![\w\[\.])(" + _WORDS + r")(?![\w\]])|\.(" + _WORDS + r")(?![\w\]])", re.IGNORECASE)


def prepare_sql(sql):
    """Берёт «рискованные» имена колонок в [скобки] (не трогая строковые литералы)."""
    parts = re.split(r"('(?:[^']|'')*')", sql)
    for i in range(0, len(parts), 2):
        parts[i] = _RISKY_RE.sub(lambda m: f"[{m.group(1)}]" if m.group(1) else f".[{m.group(2)}]", parts[i])
    return "".join(parts)


class Row(dict):
    """Строка результата: row['col'], row.col (в Jinja) и row[0]."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class Result:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount
        self._names = [d[0].lower() for d in cur.description] if cur.description else []

    def _wrap(self, r):
        return Row(zip(self._names, tuple(r)))

    def fetchone(self):
        r = self._cur.fetchone()
        return self._wrap(r) if r is not None else None

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]


class Conn:
    """Единый интерфейс поверх sqlite3 / pyodbc."""
    def __init__(self, raw, kind):
        self.raw, self.kind = raw, kind

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(prepare_sql(sql), tuple(params))
        return Result(cur)

    def insert(self, sql, params=()):
        """INSERT и возврат нового id."""
        cur = self.raw.cursor()
        cur.execute(prepare_sql(sql), tuple(params))
        if self.kind == "sqlite":
            return cur.lastrowid
        cur.execute("SELECT @@IDENTITY")
        return int(cur.fetchone()[0])

    def close(self):
        self.raw.close()


def _connect():
    if BACKEND == "sqlite":
        os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
        raw = sqlite3.connect(SQLITE_PATH)
        raw.isolation_level = None
        raw.execute("PRAGMA foreign_keys = ON")
        return Conn(raw, "sqlite")
    if pyodbc is None:
        raise RuntimeError("Для работы с Access установите pyodbc: pip install pyodbc")
    raw = pyodbc.connect(
        "DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=" + ACCESS_PATH + ";", autocommit=True)
    return Conn(raw, "access")


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
    """Атомарная транзакция + глобальная блокировка (защита от гонок за последнее место)."""
    conn = conn or get_db()
    with _LOCK:
        if conn.kind == "sqlite":
            conn.raw.execute("BEGIN IMMEDIATE")
        else:
            conn.raw.autocommit = False
        try:
            yield conn
            if conn.kind == "sqlite":
                conn.raw.execute("COMMIT")
            else:
                conn.raw.commit()
        except BaseException:
            if conn.kind == "sqlite":
                conn.raw.execute("ROLLBACK")
            else:
                conn.raw.rollback()
            raise
        finally:
            if conn.kind == "access":
                conn.raw.autocommit = True


def q_all(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def q_one(sql, params=()):
    return get_db().execute(sql, params).fetchone()


def q_val(sql, params=(), default=0):
    row = get_db().execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else default


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------------
# Создание структуры
# ----------------------------------------------------------------------------
def _statements(path):
    lines = [l for l in open(path, encoding="utf-8").read().splitlines() if not l.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def create_access_file():
    """Создаёт пустой .accdb (только Windows, драйвер Access ODBC)."""
    import ctypes
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Access (.accdb) работает только на Windows. "
                           "Для проверки на другой ОС запустите с DB_BACKEND=sqlite.")
    os.makedirs(os.path.dirname(ACCESS_PATH), exist_ok=True)
    ok = ctypes.windll.ODBCCP32.SQLConfigDataSourceW(
        0, 1, "Microsoft Access Driver (*.mdb, *.accdb)", f"CREATE_DBV12={ACCESS_PATH}\0")
    if not ok:
        raise RuntimeError("Не удалось создать файл .accdb. Создайте пустую базу в Access вручную "
                           f"и сохраните как: {ACCESS_PATH}")


def init_db(reset=False):
    """Создаёт недостающие таблицы и наполняет демо-данными пустую базу."""
    if BACKEND == "sqlite":
        os.makedirs(INSTANCE_DIR, exist_ok=True)
        if reset and os.path.exists(SQLITE_PATH):
            os.remove(SQLITE_PATH)
        conn = _connect()
        conn.raw.execute("PRAGMA journal_mode = WAL")
        conn.raw.executescript(open(SCHEMA_SQLITE, encoding="utf-8").read())
    else:
        if not os.path.exists(ACCESS_PATH):
            create_access_file()
        conn = _connect()
        existing = {r.table_name.lower() for r in conn.raw.cursor().tables(tableType="TABLE")}
        created = set()
        for stmt in _statements(SCHEMA_ACCESS):
            mt = re.match(r"CREATE\s+TABLE\s+(\w+)", stmt, re.I)
            mi = re.match(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+\w+\s+ON\s+(\w+)", stmt, re.I)
            if mt:
                target = mt.group(1).lower()
                if target in existing:        # готовые таблицы (в т.ч. созданные вручную) не трогаем
                    continue
                created.add(target)
            elif mi and mi.group(1).lower() not in created:   # индексы — только для новых таблиц
                continue
            conn.raw.cursor().execute(prepare_sql(stmt))
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        seed(conn)
    conn.close()


def seed(conn):
    """Стартовые данные: админ, тренеры, залы, тарифы, расписание и один демо-клиент."""
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@sportclub.local")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "admin123")
    stamp = now_str()

    def add_user(email, password, role):
        return conn.insert("INSERT INTO users(email, password_hash, role, is_active, created_at) VALUES (?,?,?,1,?)",
                           (email, generate_password_hash(password), role, stamp))

    with tx(conn):
        uid = add_user(admin_email, admin_pass, "admin")
        conn.execute("INSERT INTO employees(user_id, full_name, phone, position) VALUES (?,?,?,'admin')",
                     (uid, "Администратор клуба", "79990000000"))
        trainers = [
            ("trainer1@sportclub.local", "Михаил Скалин", "79373072720", "Бокс, ММА",
             "Мастер спорта по боксу. Поставит удар, научит защите и выносливости. Опыт работы с бойцами ММА более 8 лет."),
            ("trainer2@sportclub.local", "Виктория Киль", "79129609540", "Йога, пилатес",
             "Инструктор йоги и пилатеса. Эксперт по биомеханике и восстановлению позвоночника, поможет с гибкостью и осанкой."),
        ]
        trainer_ids = []
        for email, name, phone, spec, desc in trainers:
            uid = add_user(email, "trainer123", "trainer")
            trainer_ids.append(conn.insert(
                "INSERT INTO employees(user_id, full_name, phone, position, specialization, description) "
                "VALUES (?,?,?,'trainer',?,?)", (uid, name, phone, spec, desc)))

        halls = [
            ("Зона свободных весов", "Гантельный ряд от 2 до 60 кг, силовые рамы, помосты для тяги.", 40),
            ("Бойцовская зона", "Профессиональный ринг, татами для борьбы, линейка тяжёлых мешков.", 20),
            ("Зал баланса и кора", "Студия с отличной звукоизоляцией для йоги и пилатеса.", 15),
        ]
        hall_ids = [conn.insert("INSERT INTO halls(name, description, capacity) VALUES (?,?,?)", h) for h in halls]

        plans = [
            ("Месяц", 30, 2500, "Безлимитное посещение клуба и групповых занятий в течение 30 дней."),
            ("3 месяца", 90, 6500, "Выгоднее на 13% по сравнению с помесячной оплатой."),
            ("6 месяцев", 180, 11000, "Полгода тренировок без ограничений. Возможна заморозка."),
            ("Год", 365, 18000, "Максимальная выгода: экономия 12 000 ₽ по сравнению с помесячной оплатой."),
        ]
        for p in plans:
            conn.execute("INSERT INTO plans(name, duration_days, price, description, is_active) VALUES (?,?,?,?,1)", p)

        template = [("09:00", "Силовой пилатес", 1, 2, 12, 60), ("12:00", "Хатха-йога", 1, 2, 12, 75),
                    ("18:30", "Бокс: спарринги", 0, 1, 14, 90), ("20:00", "ММА для новичков", 0, 1, 14, 90)]
        today = datetime.now().date()
        for d in range(14):
            day = today + timedelta(days=d)
            if day.weekday() == 6:
                continue
            for t, title, tr, hl, places, mins in template:
                conn.execute(
                    "INSERT INTO classes(title, trainer_id, hall_id, starts_at, duration_min, max_places, status) "
                    "VALUES (?,?,?,?,?,?,'planned')",
                    (title, trainer_ids[tr], hall_ids[hl], f"{day.isoformat()} {t}", mins, places))

        uid = add_user("client@example.com", "client123", "client")
        cid = conn.insert("INSERT INTO clients(user_id, full_name, phone, registration_date) VALUES (?,?,?,?)",
                          (uid, "Иван Петров", "79001234567", today.isoformat()))
        sid = conn.insert(
            "INSERT INTO subscriptions(client_id, plan_id, start_date, end_date, status, created_at) "
            "VALUES (?,1,?,?,'active',?)", (cid, today.isoformat(), (today + timedelta(days=30)).isoformat(), stamp))
        conn.execute("INSERT INTO payments(subscription_id, amount, method, status, paid_at) VALUES (?,?,?,?,?)",
                     (sid, 2500, "card", "paid", stamp))
