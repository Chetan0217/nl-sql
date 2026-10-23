"""Runs without an API key: a scripted fake stands in for Claude.

Set NLSQL_PG_URL and/or NLSQL_MYSQL_URL to also run against Postgres and MySQL.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from nlsql import executor, safety
from nlsql.schema import introspect
from nlsql.translator import Translator, explain_sql

ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def json(self, system, messages, schema):
        self.calls.append({"system": system, "messages": list(messages)})
        return self.responses.pop(0)


def sql_reply(sql, assumptions=()):
    return {"needs_clarification": False, "clarification_question": "", "sql": sql, "assumptions": list(assumptions)}


def build(url):
    subprocess.run([sys.executable, str(ROOT / "examples/make_shop_db.py"), url], check=True, capture_output=True)
    engine = executor.make_engine(url)
    return engine, introspect(engine)


@pytest.fixture(scope="module")
def sqlite(tmp_path_factory):
    return build(f"sqlite:///{tmp_path_factory.mktemp('db') / 'shop.db'}")


PG_URL = os.environ.get("NLSQL_PG_URL")
MYSQL_URL = os.environ.get("NLSQL_MYSQL_URL")  # e.g. mysql+pymysql://root:pw@localhost/shop_test (MySQL or MariaDB)
backends = ["sqlite"] + (["postgres"] if PG_URL else []) + (["mysql"] if MYSQL_URL else [])


@pytest.fixture(scope="module", params=backends)
def db(request, sqlite):
    if request.param == "sqlite":
        return sqlite
    return build(PG_URL if request.param == "postgres" else MYSQL_URL)


# ---------------------------------------------------------------- safety

@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "UPDATE orders SET status = 'x'",
    "DROP TABLE orders",
    "INSERT INTO orders VALUES (9, 1, '2025-01-01', 'x')",
    "SELECT 1; DROP TABLE orders",
    "SELECT pg_sleep(10)",
    "SELECT * FROM orders FOR UPDATE",
    "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
    "SELECT * INTO backup FROM orders",
])
def test_blocks_unsafe(sql, sqlite):
    _, schema = sqlite
    assert not safety.check(sql, schema, "postgres").ok


def test_catches_hallucinated_names(sqlite):
    _, schema = sqlite
    bad_table = safety.check("SELECT * FROM users", schema, "sqlite")
    bad_col = safety.check("SELECT email FROM customers", schema, "sqlite")
    assert not bad_table.ok and "Unknown table" in bad_table.errors[0]
    assert not bad_col.ok and "email" in bad_col.errors[0]


def test_allows_ctes_and_caps_rows(sqlite):
    _, schema = sqlite
    r = safety.check("WITH c AS (SELECT id FROM customers) SELECT * FROM c", schema, "sqlite", max_rows=10)
    assert r.ok and "LIMIT 10" in r.sql
    kept = safety.check("SELECT id FROM customers LIMIT 3", schema, "sqlite", max_rows=10)
    assert "LIMIT 3" in kept.sql


def test_transpile_between_dialects():
    out = safety.transpile("SELECT TOP 5 name FROM customers ORDER BY name", "tsql", "postgres")
    assert "LIMIT 5" in out
    assert len(safety.SUPPORTED_DIALECTS) >= 25


def test_database_refuses_writes_even_if_checks_are_bypassed(db):
    engine, _ = db
    with pytest.raises(Exception):
        executor.run(engine, "DELETE FROM order_items")
    assert executor.run(engine, "SELECT COUNT(*) FROM order_items").rows[0][0] == 7


# ---------------------------------------------------------------- NL -> SQL

REVENUE_SQL = """
SELECT c.country, SUM(oi.quantity * p.unit_price) AS revenue
FROM orders o
JOIN customers c ON c.id = o.customer_id
JOIN order_items oi ON oi.order_id = o.id
JOIN products p ON p.id = oi.product_id
WHERE o.status = 'shipped'
GROUP BY c.country
ORDER BY revenue DESC
"""


def test_question_to_sql_runs_and_is_correct(db):
    engine, schema = db
    llm = FakeLLM(sql_reply(REVENUE_SQL, ["revenue = quantity * unit_price on shipped orders"]))
    ans = Translator.from_engine(llm, engine, schema).to_sql("Revenue by country from shipped orders?")
    assert ans.ok and ans.attempts == 1
    rows = {r[0]: float(r[1]) for r in executor.run(engine, ans.sql).rows}
    assert rows == {"IN": 1420.0, "US": 2050.0}
    # Schema grounding: the model saw real tables and sample values.
    assert "TABLE order_items" in llm.calls[0]["system"] and "'shipped'" in llm.calls[0]["system"]


def test_bad_sql_is_fed_back_and_fixed(db):
    engine, schema = db
    llm = FakeLLM(
        sql_reply("SELECT full_name FROM customers"),            # hallucinated column
        sql_reply("SELECT name FROM customer"),                  # hallucinated table
        sql_reply("SELECT name FROM customers WHERE country = 'IN'"),
    )
    ans = Translator.from_engine(llm, engine, schema).to_sql("Customers in India")
    assert ans.ok and ans.attempts == 3 and len(ans.errors) == 2
    assert "full_name" in llm.calls[1]["messages"][-1]["content"]
    assert sorted(r[0] for r in executor.run(engine, ans.sql).rows) == ["Asha Rao", "Dev Mehta"]


def test_database_level_errors_are_fed_back(db):
    """Passes static checks but fails at plan time (type error), so EXPLAIN catches it."""
    engine, schema = db
    if engine.dialect.name in ("sqlite", "mysql"):
        pytest.skip("SQLite and MySQL silently coerce types, so they don't reject this at plan time")
    llm = FakeLLM(
        sql_reply("SELECT name FROM customers WHERE created_at > 5"),
        sql_reply("SELECT name FROM customers WHERE created_at > DATE '2025-03-01'"),
    )
    ans = Translator.from_engine(llm, engine, schema).to_sql("Customers who joined after March 2025")
    assert ans.ok and ans.attempts == 2 and "Database rejected" in ans.errors[0]


def test_gives_up_after_max_attempts(sqlite):
    engine, schema = sqlite
    llm = FakeLLM(*[sql_reply("DELETE FROM orders")] * 3)
    ans = Translator.from_engine(llm, engine, schema).to_sql("delete everything")
    assert not ans.ok and ans.attempts == 3 and len(llm.calls) == 3


def test_asks_for_clarification(sqlite):
    engine, schema = sqlite
    llm = FakeLLM({"needs_clarification": True, "clarification_question": "Top by revenue or by order count?",
                   "sql": "", "assumptions": []})
    ans = Translator.from_engine(llm, engine, schema).to_sql("top customers")
    assert not ans.ok and ans.clarification.startswith("Top by")


def test_other_dialect_without_live_db(sqlite):
    """Generate for a dialect we can't connect to here; still parsed and schema-checked."""
    _, schema = sqlite
    llm = FakeLLM(sql_reply("SELECT TOP 2 name FROM customers ORDER BY created_at DESC"))
    ans = Translator(llm, schema, "tsql").to_sql("Two newest customers")
    assert ans.ok and "TOP 2" in ans.sql


# ---------------------------------------------------------------- SQL -> NL

def test_explain_grounds_with_parser_facts(sqlite):
    _, schema = sqlite
    llm = FakeLLM({"summary": "Revenue per country from shipped orders.", "steps": ["a"], "warnings": []})
    ex = explain_sql(llm, REVENUE_SQL, "sqlite", schema)
    assert ex.facts["tables"] == ["customers", "order_items", "orders", "products"]
    assert ex.facts["group_by"] == ["c.country"]
    assert "shipped" in json.dumps(ex.facts)
    assert "Facts extracted by a SQL parser" in llm.calls[0]["system"]


def test_explain_flags_dangerous_writes_without_running_them(sqlite):
    _, schema = sqlite
    llm = FakeLLM({"summary": "Deletes all orders.", "steps": [], "warnings": []})
    ex = explain_sql(llm, "DELETE FROM orders", "postgres", schema)
    assert any("without WHERE" in w for w in ex.warnings)


def test_explain_rejects_unparseable_sql():
    with pytest.raises(Exception):
        explain_sql(FakeLLM(), "SELEC * FORM x", "postgres")


def test_ollama_llm_sends_schema_and_parses_reply():
    """OllamaLLM talks to the local Ollama HTTP API; checked against a stand-in server."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from nlsql.llm import OllamaLLM

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["body"] = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            reply = _json.dumps({"message": {"role": "assistant", "content": '{"answer": 42}'}}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        schema = {"type": "object", "properties": {"answer": {"type": "integer"}}}
        llm = OllamaLLM(model="test-model", host=f"127.0.0.1:{server.server_port}")
        out = llm.json("be helpful", [{"role": "user", "content": "hi"}], schema)
    finally:
        server.shutdown()

    assert out == {"answer": 42}
    assert seen["path"] == "/api/chat"
    body = seen["body"]
    assert body["model"] == "test-model" and body["format"] == schema and body["stream"] is False
    assert body["messages"][0]["role"] == "system" and body["messages"][1] == {"role": "user", "content": "hi"}
