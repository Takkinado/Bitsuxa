-- =====================================================================
--  Спортклуб «БИЦУХА» — структура БД для Microsoft Access (.accdb)
--  Выполняется через ODBC по одному оператору (разделитель — «;»).
--  Типы Access: COUNTER = «Счётчик», INTEGER = «Длинное целое», SMALLINT = «Целое»,
--  VARCHAR(n) = «Короткий текст», LONGTEXT = «Длинный текст».
--  Даты и время хранятся текстом ISO: 'ГГГГ-ММ-ДД' и 'ГГГГ-ММ-ДД ЧЧ:ММ'.
--  Допустимые значения статусов и «один живой абонемент на клиента» контролирует код сайта
--  (CHECK-ограничения и частичные индексы через ODBC в Access недоступны).
-- =====================================================================

CREATE TABLE users (
    id            COUNTER PRIMARY KEY,
    email         VARCHAR(120) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(10)  NOT NULL,
    is_active     SMALLINT     NOT NULL,
    created_at    VARCHAR(16)  NOT NULL
);
CREATE UNIQUE INDEX ux_users_email ON users(email);

CREATE TABLE clients (
    id                COUNTER PRIMARY KEY,
    user_id           INTEGER      NOT NULL,
    full_name         VARCHAR(100) NOT NULL,
    phone             VARCHAR(11)  NOT NULL,
    registration_date VARCHAR(10)  NOT NULL,
    CONSTRAINT fk_clients_user FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE UNIQUE INDEX ux_clients_user ON clients(user_id);
CREATE UNIQUE INDEX ux_clients_phone ON clients(phone);

CREATE TABLE employees (
    id             COUNTER PRIMARY KEY,
    user_id        INTEGER      NOT NULL,
    full_name      VARCHAR(100) NOT NULL,
    phone          VARCHAR(11)  NOT NULL,
    position       VARCHAR(10)  NOT NULL,
    specialization VARCHAR(50),
    description    LONGTEXT,
    CONSTRAINT fk_employees_user FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE UNIQUE INDEX ux_employees_user ON employees(user_id);

CREATE TABLE halls (
    id          COUNTER PRIMARY KEY,
    name        VARCHAR(80) NOT NULL,
    description LONGTEXT,
    capacity    INTEGER     NOT NULL
);
CREATE UNIQUE INDEX ux_halls_name ON halls(name);

CREATE TABLE plans (
    id            COUNTER PRIMARY KEY,
    name          VARCHAR(60) NOT NULL,
    duration_days INTEGER     NOT NULL,
    price         INTEGER     NOT NULL,
    description   LONGTEXT,
    is_active     SMALLINT    NOT NULL
);
CREATE UNIQUE INDEX ux_plans_name ON plans(name);

CREATE TABLE subscriptions (
    id          COUNTER PRIMARY KEY,
    client_id   INTEGER     NOT NULL,
    plan_id     INTEGER     NOT NULL,
    start_date  VARCHAR(10),
    end_date    VARCHAR(10),
    frozen_from VARCHAR(10),
    status      VARCHAR(10) NOT NULL,
    created_at  VARCHAR(16) NOT NULL,
    CONSTRAINT fk_subs_client FOREIGN KEY (client_id) REFERENCES clients(id),
    CONSTRAINT fk_subs_plan FOREIGN KEY (plan_id) REFERENCES plans(id)
);

CREATE TABLE payments (
    id              COUNTER PRIMARY KEY,
    subscription_id INTEGER     NOT NULL,
    amount          INTEGER     NOT NULL,
    method          VARCHAR(10) NOT NULL,
    status          VARCHAR(10) NOT NULL,
    paid_at         VARCHAR(16),
    CONSTRAINT fk_pay_sub FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);
CREATE UNIQUE INDEX ux_payments_sub ON payments(subscription_id);

CREATE TABLE classes (
    id           COUNTER PRIMARY KEY,
    title        VARCHAR(100) NOT NULL,
    trainer_id   INTEGER      NOT NULL,
    hall_id      INTEGER      NOT NULL,
    starts_at    VARCHAR(16)  NOT NULL,
    duration_min INTEGER      NOT NULL,
    max_places   INTEGER      NOT NULL,
    status       VARCHAR(10)  NOT NULL,
    CONSTRAINT fk_classes_trainer FOREIGN KEY (trainer_id) REFERENCES employees(id),
    CONSTRAINT fk_classes_hall FOREIGN KEY (hall_id) REFERENCES halls(id)
);
CREATE INDEX ix_classes_starts ON classes(starts_at);

CREATE TABLE bookings (
    id         COUNTER PRIMARY KEY,
    class_id   INTEGER     NOT NULL,
    client_id  INTEGER     NOT NULL,
    status     VARCHAR(10) NOT NULL,
    created_at VARCHAR(16) NOT NULL,
    CONSTRAINT fk_book_class FOREIGN KEY (class_id) REFERENCES classes(id),
    CONSTRAINT fk_book_client FOREIGN KEY (client_id) REFERENCES clients(id)
);
CREATE UNIQUE INDEX ux_bookings_pair ON bookings(class_id, client_id);

CREATE TABLE applications (
    id         COUNTER PRIMARY KEY,
    full_name  VARCHAR(100) NOT NULL,
    phone      VARCHAR(11)  NOT NULL,
    message    LONGTEXT,
    status     VARCHAR(10)  NOT NULL,
    created_at VARCHAR(16)  NOT NULL
)
