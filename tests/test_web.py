"""The website's API, end to end on a SQLite shop database, with a fake model."""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from nlsql.rules import parse_rules, render_rules  # noqa: E402
from nlsql.web import app as web  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REVENUE_SQL = ("SELECT c.country, SUM(oi.quantity * p.unit_price) AS revenue FROM customers c "
               "JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id "
               "JOIN products p ON p.id = oi.product_id WHERE o.status <> 'cancelled' GROUP BY c.country")


class FakeLLM:
    model = "fake"

    def __init__(self):
        self.replies, self.calls = [], []

    def json(self, system, messages, schema):
        self.calls.append(system)
        return self.replies.pop(0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "shop.db"
    subprocess.run([sys.executable, str(ROOT / "examples/make_shop_db.py"), f"sqlite:///{db}"], check=True,
                   capture_output=True)
    rules = tmp_path / "rules.txt"
    rules.write_text("# comment\nExclude cancelled orders from revenue.\n", encoding="utf-8")
    monkeypatch.setenv("NLSQL_DB", f"sqlite:///{db}")
    monkeypatch.setenv("NLSQL_RULES", str(rules))
    monkeypatch.setenv("NLSQL_LLM", "ollama")
    web._state = None
    st = web.state()
    st.llm = FakeLLM()
    yield TestClient(web.app, base_url="http://127.0.0.1"), st.llm
    web._state = None


def test_page_and_status(client):
    c, _ = client
    assert "nlsql" in c.get("/").text
    s = c.get("/api/status").json()
    assert s["dialect"] == "sqlite" and "orders" in s["tables"] and s["rules"] == 1


def test_ask_runs_query_and_sends_rules(client):
    c, llm = client
    llm.replies.append({"needs_clarification": False, "clarification_question": "", "sql": REVENUE_SQL,
                        "assumptions": ["excluded cancelled orders"]})
    llm.replies.append({"answer": "India brought in 1,520 and the US 2,050."})
    r = c.post("/api/ask", json={"question": "revenue by country"}).json()
    assert r["answer"] == {"text": "India brought in 1,520 and the US 2,050.", "unverified": [], "error": None}
    assert r["ok"] and r["result"]["columns"] == ["country", "revenue"]
    rows = {row[0]: float(row[1]) for row in r["result"]["rows"]}
    assert rows == {"IN": 1520.0, "US": 2050.0}  # pending order 5 counts; cancelled order 3 does not
    assert "Exclude cancelled orders from revenue." in llm.calls[0]


def test_clarification_is_passed_through(client):
    c, llm = client
    llm.replies.append({"needs_clarification": True, "clarification_question": "By what metric?", "sql": "",
                        "assumptions": []})
    r = c.post("/api/ask", json={"question": "top customers"}).json()
    assert not r["ok"] and r["clarification"] == "By what metric?"


def test_run_blocks_writes_and_runs_reads(client):
    c, _ = client
    assert c.post("/api/run", json={"sql": "DELETE FROM orders"}).status_code == 400
    r = c.post("/api/run", json={"sql": "SELECT COUNT(*) AS n FROM orders"}).json()
    assert r["result"]["rows"] == [[5]]


def test_explain(client):
    c, llm = client
    llm.replies.append({"summary": "Revenue per country.", "steps": ["join", "group"], "warnings": []})
    r = c.post("/api/explain", json={"sql": REVENUE_SQL}).json()
    assert r["summary"] == "Revenue per country." and r["safe_to_run"] is True
    assert "Business rules" in llm.calls[0]
    assert c.post("/api/explain", json={"sql": "SELEC * FORM orders"}).status_code == 400


def test_rules_can_be_edited(client):
    c, _ = client
    r = c.put("/api/rules", json={"text": "- Rule one\n\n# skip\nRule two\n"}).json()
    assert r["rules"] == ["Rule one", "Rule two"]
    assert c.get("/api/status").json()["rules"] == 2


def test_model_down_gives_clear_error(client):
    c, llm = client

    def boom(*a):
        raise RuntimeError("Cannot reach Ollama at http://localhost:11434. Is the Ollama app running?")

    llm.json = boom
    r = c.post("/api/ask", json={"question": "anything"})
    assert r.status_code == 502 and "Ollama" in r.json()["detail"]


def test_other_hosts_are_refused(client):
    c, _ = client
    assert c.get("/api/status", headers={"host": "evil.example"}).status_code == 400


def test_rules_parsing():
    assert parse_rules("# c\n\n- a\n* b\nc") == ["a", "b", "c"]
    assert render_rules([]) == ""
    assert "- a" in render_rules(["a"])


def test_cells_keep_money_exact():
    import datetime as dt
    from decimal import Decimal

    assert web.cell(Decimal("73906449.55")) == "73906449.55"
    assert web.cell(dt.date(2025, 1, 2)) == "2025-01-02"
    assert web.cell(dt.datetime(2025, 1, 2, 3, 4)) == "2025-01-02 03:04:00"
    assert web.cell(None) is None and web.cell(7) == 7


def test_format(client):
    c, _ = client
    r = c.post("/api/format", json={"sql": "select a,b from t where x=1"}).json()
    assert r["sql"].startswith("SELECT") and "\n" in r["sql"]
    assert c.post("/api/format", json={"sql": "SELEC * FORM t"}).status_code == 400


def test_answer_flags_invented_numbers(client):
    c, llm = client
    llm.replies.append({"answer": "Laptops made 80 million, about 45% of sales."})
    r = c.post("/api/answer", json={"question": "top category", "sql": "SELECT 1",
                                    "columns": ["category", "revenue"], "rows": [["Laptops", "73906449.55"]]}).json()
    assert r["unverified"] == ["80 million", "45%"]
    llm.replies.append({"answer": "Laptops earned the most, about 73.9 million."})
    r = c.post("/api/answer", json={"question": "top category", "sql": "SELECT 1",
                                    "columns": ["category", "revenue"], "rows": [["Laptops", "73906449.55"]]}).json()
    assert r["unverified"] == []


def test_answer_failure_keeps_rows(client):
    c, llm = client
    llm.replies.append({"needs_clarification": False, "clarification_question": "", "sql": "SELECT COUNT(*) AS n FROM orders",
                        "assumptions": []})
    r = c.post("/api/ask", json={"question": "how many orders"}).json()  # no reply queued for the answer call
    assert r["result"]["rows"] == [[5]] and r["answer"]["text"] is None and r["answer"]["error"]
