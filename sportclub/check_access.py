"""Проверка готовой базы Access: есть ли все таблицы и подходят ли типы полей.

    python check_access.py            # проверяет data/Спортклуб.accdb (или путь из ACCESS_DB)

Ничего не меняет в базе. Если найдены расхождения — исправьте тип поля в конструкторе таблицы Access.
"""
import os
import re
import sys

import db

TEXT = {"VARCHAR", "LONGCHAR", "CHAR", "LONGTEXT", "TEXT", "WVARCHAR"}
INT = {"INTEGER", "SMALLINT", "COUNTER", "BYTE", "TINYINT", "LONG", "SHORT", "BIT"}


def expected_schema():
    """{таблица: {поле: 'text'|'int'}} из schema_access.sql."""
    out = {}
    for stmt in db._statements(db.SCHEMA_ACCESS):
        m = re.match(r"CREATE\s+TABLE\s+(\w+)\s*\((.*)\)\s*$", stmt, re.S | re.I)
        if not m:
            continue
        cols = {}
        for line in m.group(2).splitlines():
            line = line.strip().rstrip(",")
            mc = re.match(r"(\w+)\s+(COUNTER|INTEGER|SMALLINT|VARCHAR|LONGTEXT)\b", line, re.I)
            if mc and not line.upper().startswith("CONSTRAINT"):
                kind = "int" if mc.group(2).upper() in ("COUNTER", "INTEGER", "SMALLINT") else "text"
                cols[mc.group(1).lower()] = kind
        out[m.group(1).lower()] = cols
    return out


def main():
    if db.BACKEND != "access":
        print("Запустите без DB_BACKEND=sqlite")
        return 1
    if not os.path.exists(db.ACCESS_PATH):
        print("Файл не найден:", db.ACCESS_PATH)
        return 1
    conn = db._connect()
    cur = conn.raw.cursor()
    have = {r.table_name.lower(): r.table_name for r in cur.tables(tableType="TABLE")}
    problems = []
    for table, cols in expected_schema().items():
        if table not in have:
            print(f"  таблицы «{table}» нет — её создаст init_access.py")
            continue
        real = {c.column_name.lower(): c.type_name.upper() for c in conn.raw.cursor().columns(table=have[table])}
        for col, kind in cols.items():
            if col not in real:
                problems.append(f"{table}: нет поля «{col}»")
                continue
            t = real[col]
            ok = (t in TEXT) if kind == "text" else (t in INT)
            if not ok:
                need = "Короткий/Длинный текст" if kind == "text" else "Число / Счётчик"
                problems.append(f"{table}.{col}: тип {t}, нужен {need}")
        extra = set(real) - set(cols)
        if extra:
            print(f"  (в «{table}» есть лишние поля, они не мешают: {', '.join(sorted(extra))})")
        n = cur.execute(f"SELECT COUNT(*) FROM [{have[table]}]").fetchone()[0]
        print(f"  таблица {table}: записей {n}")
    print()
    if problems:
        print("НАЙДЕНЫ ПРОБЛЕМЫ:")
        for p in problems:
            print(" -", p)
        return 2
    print("OK: структура базы подходит для сайта.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
