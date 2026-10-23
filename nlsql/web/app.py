"""The local website: a small FastAPI server that wraps the nlsql package.

The browser never talks to the database or the model directly. It sends JSON to
the endpoints below; this file calls the same Translator / explain_sql / executor
code the command line uses, and sends JSON back.

    browser (static/index.html + app.js)
        │  fetch("/api/ask", {question})
        ▼
    this server ──► Translator.to_sql ──► Ollama (local model)
        │                    └──────────► safety checks + EXPLAIN on MySQL
        └──► executor.run (read-only) ──► rows back to the browser

Settings come from environment variables, so no password is ever written in code:
    NLSQL_DB     SQLAlchemy URL of the database (required)
    NLSQL_RULES  business rules file (default: examples/business_rules.txt if it exists)
    NLSQL_LLM    'ollama' (default here) or 'claude'
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.engine import make_url

from .. import executor, safety
from ..llm import ModelRefused, OllamaLLM, make_llm
from ..rules import load_rules, parse_rules
from ..schema import introspect
from ..translator import Translator, answer_in_words, explain_sql, sqlglot_dialect

STATIC = Path(__file__).parent / "static"
MAX_ROWS = 1000  # rows the database may return for one query


# ------------------------------------------------------------------ app state


class State:
    """Everything the server sets up once at start: database, schema, model, rules."""

    def __init__(self) -> None:
        url = os.environ.get("NLSQL_DB")
        if not url:
            raise SystemExit(
                "Set NLSQL_DB to your database URL first, for example (PowerShell):\n"
                '  $env:NLSQL_DB = "mysql+pymysql://nlsql_ro:<password>@localhost/nlsql_demo"'
            )
        os.environ.setdefault("NLSQL_LLM", "ollama")  # free local model unless told otherwise

        self.url_for_display = make_url(url).render_as_string(hide_password=True)
        self.engine = executor.make_engine(url)
        self.dialect = sqlglot_dialect(self.engine)
        self.llm = make_llm()
        self.rules_path = Path(os.environ.get("NLSQL_RULES") or "examples/business_rules.txt")
        self.rules = load_rules(self.rules_path)
        self.lock = threading.Lock()  # guards schema/rules swaps while requests run
        self.reload_schema()

    def reload_schema(self) -> None:
        schema = introspect(self.engine, sample_values=3)
        with self.lock:
            self.schema = schema

    def translator(self) -> Translator:
        # Cheap to build; building per request means rule edits apply immediately.
        with self.lock:
            return Translator(self.llm, self.schema, self.dialect, engine=self.engine,
                              max_rows=MAX_ROWS, rules=self.rules)


_state: State | None = None


def state() -> State:
    global _state
    if _state is None:
        _state = State()
    return _state


# ------------------------------------------------------------------ helpers


def cell(v):
    """Turn one database value into something JSON can carry without losing precision."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return str(v)  # keep exact money values; a float would round 73906449.55
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return str(v)


def run_query(sql: str) -> dict:
    res = executor.run(state().engine, sql)
    return {
        "columns": res.columns,
        "rows": [[cell(v) for v in row] for row in res.rows],
        "row_count": len(res.rows),
        "capped": len(res.rows) >= MAX_ROWS,
    }


def db_error(e: Exception) -> str:
    return str(getattr(e, "orig", e)).strip().splitlines()[0]


def model_call(fn):
    """Run something that calls the model; turn model failures into clear HTTP errors."""
    try:
        return fn()
    except ModelRefused as e:
        raise HTTPException(422, f"The model declined: {e}") from e
    except RuntimeError as e:  # e.g. Ollama not running
        raise HTTPException(502, str(e)) from e
    except json.JSONDecodeError as e:
        raise HTTPException(502, f"The model returned text that is not valid JSON: {e}") from e


# ------------------------------------------------------------------ request shapes


class AskIn(BaseModel):
    question: str
    run: bool = True
    check_meaning: bool = False
    answer: bool = True  # also describe the result in plain words (one more model call)


class AnswerIn(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[list]


class SQLIn(BaseModel):
    sql: str
    dialect: str | None = None


class RulesIn(BaseModel):
    text: str


# ------------------------------------------------------------------ the app


app = FastAPI(title="nlsql", description="Plain language ⇄ SQL, running on your own machine.")
# Only answer requests addressed to this machine. Blocks "DNS rebinding" tricks
# where a web page you visit tries to reach your local server under another name.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    s = state()
    llm = {"backend": type(s.llm).__name__, "model": getattr(s.llm, "model", "?"), "reachable": None}
    if isinstance(s.llm, OllamaLLM):
        try:
            with urllib.request.urlopen(f"{s.llm.host}/api/tags", timeout=2) as r:
                names = [m["name"] for m in json.loads(r.read()).get("models", [])]
            llm["reachable"] = True
            llm["model_installed"] = s.llm.model in names or f"{s.llm.model}:latest" in names
        except Exception:
            llm["reachable"] = False
    return {
        "database": s.url_for_display,
        "dialect": s.dialect,
        "tables": [t.name for t in s.schema.tables],
        "llm": llm,
        "rules": len(s.rules),
    }


@app.get("/api/schema")
def get_schema():
    s = state()
    return {
        "text": s.schema.render(),
        "tables": [
            {
                "name": t.name,
                "comment": t.comment,
                "columns": [{"name": c.name, "type": c.type, "pk": c.primary_key, "samples": c.samples}
                            for c in t.columns],
            }
            for t in s.schema.tables
        ],
    }


@app.post("/api/schema/reload")
def reload_schema():
    state().reload_schema()
    return get_schema()


@app.post("/api/ask")
def ask(body: AskIn):
    """Plain language -> checked SQL, and optionally the rows it returns."""
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Type a question first.")
    t0 = time.perf_counter()
    ans = model_call(lambda: state().translator().to_sql(question, check_meaning=body.check_meaning))
    out = {
        "ok": ans.ok,
        "sql": ans.sql,
        "assumptions": ans.assumptions,
        "clarification": ans.clarification,
        "attempts": ans.attempts,
        "errors": ans.errors,
        "meaning": ans.meaning,
        "result": None,
        "run_error": None,
        "answer": None,
    }
    if ans.ok and body.run:
        try:
            out["result"] = run_query(ans.sql)
        except Exception as e:  # noqa: BLE001 - shown to the user
            out["run_error"] = db_error(e)
    if out["result"] is not None and body.answer:
        res = out["result"]
        out["answer"] = describe(question, ans.sql, res["columns"], res["rows"])
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


def describe(question: str, sql: str, columns: list[str], rows: list) -> dict:
    """Result rows -> a sentence or two. A failure here never hides the rows themselves."""
    try:
        a = answer_in_words(state().llm, question, sql, columns, rows)
        return {"text": a.text, "unverified": a.unverified, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"text": None, "unverified": [], "error": str(e)}


@app.post("/api/answer")
def answer(body: AnswerIn):
    """Describe rows the page already has (used after re-running SQL from history)."""
    return describe(body.question, body.sql, body.columns, body.rows)


@app.post("/api/run")
def run(body: SQLIn):
    """Run SQL the user typed or edited. Same safety checks as generated SQL."""
    s = state()
    checked = safety.check(body.sql.strip().rstrip(";"), s.schema, s.dialect, MAX_ROWS)
    if not checked.ok:
        raise HTTPException(400, "Blocked: " + " ".join(checked.errors))
    try:
        return {"sql": checked.sql, "result": run_query(checked.sql)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Database error: {db_error(e)}") from e


@app.post("/api/explain")
def explain(body: SQLIn):
    """SQL -> plain language. Never runs the SQL."""
    s = state()
    dialect = body.dialect or s.dialect
    if dialect not in safety.SUPPORTED_DIALECTS:
        raise HTTPException(400, f"Unknown dialect {dialect!r}.")
    sql = body.sql.strip()
    if not sql:
        raise HTTPException(400, "Paste a SQL query first.")
    try:
        safety.parse_one(sql, dialect)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not parse this as {dialect} SQL: {e}") from e
    with s.lock:
        schema, rules = s.schema, list(s.rules)
    t0 = time.perf_counter()
    ex = model_call(lambda: explain_sql(s.llm, sql, dialect, schema, rules=rules))
    check = safety.check(sql, schema, dialect, None)
    return {
        "summary": ex.summary,
        "steps": ex.steps,
        "warnings": ex.warnings,
        "facts": ex.facts,
        "safe_to_run": check.ok,
        "seconds": round(time.perf_counter() - t0, 1),
    }


@app.post("/api/format")
def format_sql(body: SQLIn):
    """Re-indent SQL with the parser. No model call; works on any statement it can parse."""
    dialect = body.dialect or state().dialect
    try:
        return {"sql": safety.transpile(body.sql, dialect, dialect)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not parse this as {dialect} SQL: {e}") from e


@app.get("/api/dialects")
def dialects():
    return safety.SUPPORTED_DIALECTS


@app.get("/api/rules")
def get_rules():
    s = state()
    text = s.rules_path.read_text(encoding="utf-8") if s.rules_path.exists() else ""
    return {"path": str(s.rules_path.resolve()), "text": text, "rules": s.rules}


@app.put("/api/rules")
def put_rules(body: RulesIn):
    s = state()
    s.rules_path.parent.mkdir(parents=True, exist_ok=True)
    s.rules_path.write_text(body.text, encoding="utf-8")
    with s.lock:
        s.rules = parse_rules(body.text)
    return get_rules()
