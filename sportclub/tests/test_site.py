import os, re, sys, tempfile
os.environ["DB_BACKEND"] = "sqlite"; os.environ["SPORTCLUB_DB"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
from db import _connect
c = A.app.test_client()

def tok(html):
    m = re.search(r'name="_csrf" value="([^"]+)"', html); return m.group(1)
def get(url, **k): return c.get(url, follow_redirects=True, **k)
def post(url, data=None, page=None, **k):
    t = tok(get(page or "/").get_data(as_text=True))
    d = dict(data or {}); d["_csrf"] = t
    return c.post(url, data=d, follow_redirects=True, **k)
def txt(r): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.get_data(as_text=True)))
ok = lambda cond, name: print(("PASS " if cond else "FAIL ") + name) or (cond or sys.exit(1))

# Публичные
for u in ["/", "/schedule", "/login", "/register"]:
    ok(get(u).status_code == 200, f"GET {u}")
ok("Купить" in get("/").get_data(as_text=True), "тарифы на главной")

# Форма заявки -> БД
r = post("/apply", {"full_name": "Гость Тестов", "phone": "8 (900) 111-22-33", "message": "хочу бокс"})
ok("Заявка принята" in r.get_data(as_text=True), "заявка принята")
db = _connect()
row = db.execute("select * from applications").fetchone()
ok(row and row["phone"] == "79001112233", "заявка сохранилась в БД, телефон нормализован")
r = post("/apply", {"full_name": "X", "phone": "123"}); ok("Укажите имя" in r.get_data(as_text=True), "валидация заявки")
post("/apply", {"full_name": "Гость Тестов", "phone": "89001112233"})
ok(db.execute("select count(*) from applications").fetchone()[0] == 1, "дубль заявки за 10 мин не создаётся")

# CSRF
r = c.post("/apply", data={"full_name": "A B", "phone": "89001112233"}); ok(r.status_code == 400, "POST без CSRF отклонён")

# Доступ без входа
r = c.get("/cabinet"); ok(r.status_code == 302 and "/login" in r.location, "кабинет требует входа")
r = c.get("/admin/"); ok(r.status_code == 302, "админка требует входа")

# Регистрация
r = post("/register", {"full_name": "Анна Иванова", "phone": "+7 999 555-44-33", "email": "anna@test.ru", "password": "secret1", "password2": "secret1"}, page="/register")
ok("Регистрация прошла" in txt(r), "регистрация клиента")
ok(db.execute("select role from users where email='anna@test.ru'").fetchone()["role"] == "client", "users.role=client")
ok(db.execute("select phone from clients where full_name='Анна Иванова'").fetchone()[0] == "79995554433", "clients сохранён")
r = c.get("/admin/"); ok(r.status_code == 403, "клиент не попадает в админку (403)")

# Запись без абонемента
cls = db.execute("select id from classes where starts_at > datetime('now','localtime') and status='planned' order by starts_at limit 1").fetchone()[0]
r = post(f"/book/{cls}", page="/schedule"); ok("нужен абонемент" in txt(r), "запись без абонемента запрещена")

# Покупка наличными -> pending
r = post("/buy/2", {"method": "cash"}, page="/buy/2")
ok("Оплатите его на ресепшене" in txt(r), "покупка (касса) -> pending")
r = post("/buy/1", {"method": "card"}, page="/buy/1"); ok("уже есть абонемент" in txt(r), "второй абонемент заблокирован")
r = post(f"/book/{cls}", page="/schedule"); ok("ещё не оплачен" in txt(r), "запись при неоплаченном абонементе запрещена")

# Админ подтверждает
c.post("/logout", data={"_csrf": tok(get("/").get_data(as_text=True))})
r = post("/login", {"email": "admin@sportclub.local", "password": "admin123"}, page="/login"); ok("Обзор" in txt(r), "вход админа")
sid = db.execute("select id from subscriptions where status='pending'").fetchone()[0]
r = post(f"/admin/subscriptions/{sid}/confirm", page="/admin/"); ok("Оплата подтверждена" in txt(r), "админ подтвердил оплату")
s = db.execute("select * from subscriptions where id=?", (sid,)).fetchone()
ok(s["status"] == "active" and s["end_date"] > s["start_date"], "абонемент активен, даты проставлены")
ok(db.execute("select status from payments where subscription_id=?", (sid,)).fetchone()[0] == "paid", "платёж paid")
for u in ["/admin/", "/admin/applications", "/admin/clients", "/admin/clients?q=Анна", "/admin/subscriptions", "/admin/subscriptions?status=active",
          "/admin/plans", "/admin/halls", "/admin/trainers", "/admin/classes", "/admin/classes?show=all", "/admin/clients/new", "/admin/plans/new",
          "/admin/halls/new", "/admin/trainers/new", "/admin/classes/new", f"/admin/classes/{cls}", f"/admin/classes/{cls}/edit",
          "/admin/clients/1", "/admin/clients/1/edit", "/admin/plans/1/edit", "/admin/halls/1/edit", "/admin/trainers/2/edit"]:
    ok(get(u).status_code == 200, f"admin GET {u}")

# Админ: тариф CRUD + валидация
r = post("/admin/plans/new", {"name": "Тест", "duration_days": "10", "price": "-5"}, page="/admin/plans/new"); ok("отрицательной" in txt(r), "валидация цены")
r = post("/admin/plans/new", {"name": "Тест", "duration_days": "10", "price": "500", "is_active": "1"}, page="/admin/plans/new"); ok("Тариф добавлен" in txt(r), "тариф создан")
pid = db.execute("select id from plans where name='Тест'").fetchone()[0]
r = post(f"/admin/plans/{pid}/delete", page="/admin/plans"); ok("Тариф удалён" in txt(r), "неиспользуемый тариф удалён")
r = post("/admin/plans/2/delete", page="/admin/plans"); ok("можно только скрыть" in txt(r), "используемый тариф не удаляется")

# Админ: расписание — конфликты
from datetime import datetime, timedelta
t = (datetime.now() + timedelta(days=20)).replace(hour=10, minute=0, second=0, microsecond=0)
base = {"title": "Тест-занятие", "trainer_id": "2", "hall_id": "2", "starts_at": t.strftime("%Y-%m-%dT%H:%M"), "duration_min": "60", "max_places": "10"}
r = post("/admin/classes/new", base, page="/admin/classes/new"); ok("добавлено в расписание" in txt(r), "занятие создано")
r = post("/admin/classes/new", base, page="/admin/classes/new"); ok("уже есть другое занятие" in txt(r) and "занят" in txt(r), "конфликт тренера и зала")
r = post("/admin/classes/new", {**base, "trainer_id": "3", "hall_id": "3", "max_places": "99"}, page="/admin/classes/new"); ok("больше вместимости" in txt(r), "места > вместимости зала")
r = post("/admin/classes/new", {**base, "starts_at": "2020-01-01T10:00"}, page="/admin/classes/new"); ok("в прошлом" in txt(r), "занятие в прошлом запрещено")

# Админ: тренер
r = post("/admin/trainers/new", {"full_name": "Пётр Петров", "phone": "89001234000", "specialization": "Кроссфит", "email": "p@t.ru", "password": "pass123"}, page="/admin/trainers/new")
ok("Тренер добавлен" in txt(r), "тренер создан"); 

# Клиент: запись
c.post("/logout", data={"_csrf": tok(get("/").get_data(as_text=True))})
post("/login", {"email": "anna@test.ru", "password": "secret1"}, page="/login")
ok("Активен" in txt(get("/cabinet")), "кабинет: абонемент активен")
r = post(f"/book/{cls}", page="/schedule"); ok("Вы записаны" in txt(r), "запись на занятие")
r = post(f"/book/{cls}", page="/schedule"); ok("уже записаны" in txt(r), "повторная запись запрещена")
# конфликт по времени: другое занятие в тот же час
other = db.execute("select id from classes where id<>? and starts_at=(select starts_at from classes where id=?)", (cls, cls)).fetchone()
if other:
    r = post(f"/book/{other[0]}", page="/schedule"); ok("в это время" in txt(r), "пересечение записей клиента запрещено")
# заморозка
sid = db.execute("select id from subscriptions where client_id=(select id from clients where full_name='Анна Иванова')").fetchone()[0]
r = post(f"/cabinet/subscription/{sid}/freeze", page="/cabinet"); ok("заморожен" in txt(r), "заморозка")
ok(db.execute("select count(*) from bookings where status='booked' and client_id=(select id from clients where full_name='Анна Иванова')").fetchone()[0] == 0, "при заморозке будущие записи отменены")
r = post(f"/book/{cls}", page="/schedule"); ok("заморожен" in txt(r), "запись при заморозке запрещена")
r = post(f"/cabinet/subscription/{sid}/unfreeze", page="/cabinet"); ok("разморожен" in txt(r), "разморозка")
# чужое
r = post("/cabinet/subscription/1/freeze", page="/cabinet"); ok(r.status_code == 404, "чужой абонемент недоступен (404)")
# смена пароля
r = post("/cabinet/password", {"old_password": "secret1", "new_password": "newpass1", "new_password2": "newpass1"}, page="/cabinet"); ok("Пароль изменён" in txt(r), "смена пароля")
# вместимость: заполняем занятие
db.execute("update classes set max_places=1 where id=?", (cls,))
r = post(f"/book/{cls}", page="/schedule"); ok("Вы записаны" in txt(r), "последнее место занято")
# Тренер видит клиентов
c.post("/logout", data={"_csrf": tok(get("/").get_data(as_text=True))})
temail = db.execute("select u.email from classes c join employees e on e.id=c.trainer_id join users u on u.id=e.user_id where c.id=?", (cls,)).fetchone()[0]
post("/login", {"email": temail, "password": "trainer123"}, page="/login")
page = get("/trainer")
ok("Анна Иванова" in txt(page), "тренер видит записавшихся")
ok(c.get("/admin/").status_code == 403, "тренер не в админке")
# Открытый редирект
c.post("/logout", data={"_csrf": tok(get("/").get_data(as_text=True))})
t = tok(get("/login").get_data(as_text=True))
r = c.post("/login?next=//evil.com", data={"_csrf": t, "email": "client@example.com", "password": "client123"})
ok("evil.com" not in (r.location or ""), "открытый редирект через next блокируется")
c.post("/logout", data={"_csrf": tok(get("/").get_data(as_text=True))})
# Ограничения на уровне БД
import sqlite3
cid = db.execute("select id from clients where full_name='Иван Петров'").fetchone()[0]
try:
    db.execute("insert into subscriptions(client_id, plan_id, start_date, end_date, status) values (?,1,'2026-01-01','2026-02-01','active')", (cid,)); ok(False, "2 живых абонемента")
except sqlite3.IntegrityError: ok(True, "БД: второй живой абонемент невозможен (partial unique index)")
try:
    db.execute("insert into subscriptions(client_id, plan_id, status) values (?,1,'active')", (cid,)); ok(False, "active без дат")
except sqlite3.IntegrityError: ok(True, "БД: active без дат невозможен (CHECK)")
try:
    db.execute("insert into users(email,password_hash,role) values ('x@x.x','h','boss')"); ok(False, "роль")
except sqlite3.IntegrityError: ok(True, "БД: недопустимая роль невозможна (CHECK)")
try:
    db.execute("delete from halls where id=2"); ok(False, "fk")
except sqlite3.IntegrityError: ok(True, "БД: нельзя удалить зал с занятиями (FK RESTRICT)")
# Целостность БД
ic = db.execute("pragma integrity_check").fetchone()[0]; fk = db.execute("pragma foreign_key_check").fetchall()
ok(ic == "ok" and not fk, "integrity_check и foreign_key_check чистые")
# Админ отменяет занятие -> записи отменены
c.post("/logout", data={"_csrf": tok(get("/").get_data(as_text=True))})
post("/login", {"email": "admin@sportclub.local", "password": "admin123"}, page="/login")
r = post(f"/admin/classes/{cls}/cancel", page="/admin/classes"); ok("Отменено записей клиентов: 1" in txt(r), "отмена занятия каскадно отменяет записи")
r = post(f"/admin/classes/{cls}/delete", page="/admin/classes"); ok("нельзя" in txt(r), "удаление занятия с записями запрещено")
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
