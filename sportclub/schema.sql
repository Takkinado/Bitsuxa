-- =====================================================================
--  Спортклуб «БИЦУХА» — схема БД (SQLite, 3НФ)
--  Единый источник правды для сайта, ER-диаграммы и UML.
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------- Учётные записи (авторизация) ----------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('client','trainer','admin')),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ---------- Клиенты (1:1 с users) ----------
CREATE TABLE IF NOT EXISTS clients (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    full_name         TEXT    NOT NULL,
    phone             TEXT    NOT NULL UNIQUE,
    registration_date TEXT    NOT NULL DEFAULT (date('now','localtime'))
);

-- ---------- Сотрудники: администраторы и тренеры (1:1 с users) ----------
CREATE TABLE IF NOT EXISTS employees (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    full_name      TEXT    NOT NULL,
    phone          TEXT    NOT NULL,
    position       TEXT    NOT NULL CHECK (position IN ('admin','trainer')),
    specialization TEXT,          -- заполняется только у тренеров
    description    TEXT,
    CHECK (position = 'trainer' OR specialization IS NULL)
);

-- ---------- Залы ----------
CREATE TABLE IF NOT EXISTS halls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    capacity    INTEGER NOT NULL CHECK (capacity > 0)
);

-- ---------- Тарифы (то, что продаётся на сайте) ----------
CREATE TABLE IF NOT EXISTS plans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    duration_days INTEGER NOT NULL CHECK (duration_days > 0),
    price         INTEGER NOT NULL CHECK (price >= 0),      -- в рублях
    description   TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

-- ---------- Абонементы (у клиента их может быть много за всё время) ----------
CREATE TABLE IF NOT EXISTS subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    plan_id     INTEGER NOT NULL REFERENCES plans(id)   ON DELETE RESTRICT,
    start_date  TEXT,               -- проставляется при оплате
    end_date    TEXT,
    frozen_from TEXT,               -- дата начала заморозки (если frozen)
    status      TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','active','frozen','expired','cancelled')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (status IN ('pending','cancelled') OR (start_date IS NOT NULL AND end_date IS NOT NULL)),
    CHECK (end_date IS NULL OR end_date >= start_date)
);
-- У клиента не может быть двух «живых» абонементов одновременно
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_live_subscription
    ON subscriptions(client_id) WHERE status IN ('pending','active','frozen');

-- ---------- Платежи (оплата абонемента) ----------
CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL UNIQUE REFERENCES subscriptions(id) ON DELETE CASCADE,
    amount          INTEGER NOT NULL CHECK (amount >= 0),
    method          TEXT    NOT NULL CHECK (method IN ('card','cash')),
    status          TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paid','refunded')),
    paid_at         TEXT,
    CHECK (status <> 'paid' OR paid_at IS NOT NULL)
);

-- ---------- Расписание занятий ----------
CREATE TABLE IF NOT EXISTS classes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    trainer_id   INTEGER NOT NULL REFERENCES employees(id) ON DELETE RESTRICT,
    hall_id      INTEGER NOT NULL REFERENCES halls(id)     ON DELETE RESTRICT,
    starts_at    TEXT    NOT NULL,             -- 'YYYY-MM-DD HH:MM'
    duration_min INTEGER NOT NULL DEFAULT 60 CHECK (duration_min > 0),
    max_places   INTEGER NOT NULL CHECK (max_places > 0),
    status       TEXT    NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_classes_starts ON classes(starts_at);

-- ---------- Записи клиентов на занятия ----------
CREATE TABLE IF NOT EXISTS bookings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id   INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    status     TEXT    NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','cancelled')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (class_id, client_id)
);

-- ---------- Заявки с сайта (форма «Пробное занятие» без регистрации) ----------
CREATE TABLE IF NOT EXISTS applications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name  TEXT NOT NULL,
    phone      TEXT NOT NULL,
    message    TEXT,
    status     TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','processed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
