"""Проверяет, что весь SQL в проекте написан в диалекте, понятном Microsoft Access."""
import ast, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = [(r"\bLIMIT\b", "LIMIT (в Access — TOP)"), (r"\bCOALESCE\b", "COALESCE (в Access — Nz)"),
             (r"\|\|", "|| (в Access — &)"), (r"\bON CONFLICT\b", "ON CONFLICT"), (r"\bCASE\b", "CASE"),
             (r"\bdatetime\s*\(", "datetime()"), (r"\bdate\s*\(", "date()"), (r"\bstrftime\b", "strftime"),
             (r"\bILIKE\b", "ILIKE"), (r"\bRETURNING\b", "RETURNING")]
SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.I)
bad = []; checked = 0
for fn in ("app.py", "admin.py", "auth.py", "logic.py", "db.py"):
    tree = ast.parse(open(os.path.join(ROOT, fn), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL_START.match(node.value):
            sql = re.sub(r"'(?:[^']|'')*'", "''", node.value)      # без строковых литералов
            checked += 1
            for pat, name in FORBIDDEN:
                if re.search(pat, sql, re.I):
                    bad.append(f"{fn}:{node.lineno}: {name}: {node.value[:70]!r}")
            for m in re.finditer(r"\bJOIN\b", sql, re.I):
                if not re.search(r"\b(INNER|LEFT)\s+$", sql[:m.start()], re.I):
                    bad.append(f"{fn}:{node.lineno}: JOIN без INNER/LEFT: {node.value[:70]!r}")
            for part in re.split(r"\bFROM\b", sql, flags=re.I)[1:]:
                clause = re.split(r"\bWHERE\b|\bORDER BY\b|\bGROUP BY\b", part, flags=re.I)[0]
                joins = len(re.findall(r"\bJOIN\b", clause, re.I))
                if joins and "(SELECT" not in clause.upper():
                    lead = len(re.match(r"\s*(\(*)", clause).group(1))
                    if lead != joins - 1:
                        bad.append(f"{fn}:{node.lineno}: для {joins} JOIN нужно {joins-1} скобок после FROM, а их {lead}: {node.value[:70]!r}")
print(f"проверено SQL-строк: {checked}")
if bad:
    print("\n".join(bad)); sys.exit(1)
print("OK: SQL совместим с диалектом Access")
