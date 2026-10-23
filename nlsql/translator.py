"""NL -> SQL and SQL -> NL, grounded in a real schema and checked before use."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlglot import exp

from . import executor, safety
from .llm import LLM
from .rules import render_rules
from .schema import Schema

# SQLAlchemy dialect name -> sqlglot dialect name, where they differ.
_SA_TO_SQLGLOT = {"postgresql": "postgres", "mssql": "tsql", "mariadb": "mysql"}


def sqlglot_dialect(engine: Engine) -> str:
    return _SA_TO_SQLGLOT.get(engine.dialect.name, engine.dialect.name)


# ---------------------------------------------------------------- NL -> SQL

NL2SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"type": "string"},
        "sql": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["needs_clarification", "clarification_question", "sql", "assumptions"],
    "additionalProperties": False,
}

NL2SQL_SYSTEM = """You translate a user's question into one SQL query for a {dialect} database.

Database schema (the only tables and columns that exist):
{schema}
{business_rules}
Rules:
- Write exactly one read-only query (SELECT or WITH ... SELECT) in {dialect} syntax.
- Use only tables and columns from the schema above. Never invent names.
- Prefer explicit JOIN ... ON using the foreign keys shown. Qualify columns with table aliases when more than one table is involved.
- Give output columns readable aliases.
- If the question is genuinely ambiguous in a way that would change the answer (for example "top customers" with no metric, or a column that does not exist), set needs_clarification to true, ask one short question in clarification_question, and leave sql empty.
- Otherwise set needs_clarification to false and clarification_question to "". List any reasonable interpretation you picked in assumptions (for example "revenue = sum of quantity * unit_price").
- Do not add a LIMIT unless the question asks for a number of rows; the system caps rows itself."""


@dataclass
class SQLAnswer:
    sql: str | None
    dialect: str
    assumptions: list[str] = field(default_factory=list)
    clarification: str | None = None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    meaning: str | None = None  # independent back-translation, when requested

    @property
    def ok(self) -> bool:
        return self.sql is not None


class Translator:
    def __init__(
        self,
        llm: LLM,
        schema: Schema,
        dialect: str,
        engine: Engine | None = None,
        max_rows: int = 1000,
        max_attempts: int = 3,
        rules: list[str] | None = None,
    ):
        if dialect not in safety.SUPPORTED_DIALECTS:
            raise ValueError(f"Unknown dialect {dialect!r}. Supported: {', '.join(safety.SUPPORTED_DIALECTS)}")
        self.llm = llm
        self.schema = schema
        self.dialect = dialect
        self.engine = engine
        self.max_rows = max_rows
        self.max_attempts = max_attempts
        self.rules = list(rules or [])
        self._schema_text = schema.render()

    @classmethod
    def from_engine(cls, llm: LLM, engine: Engine, schema: Schema, **kw) -> "Translator":
        return cls(llm, schema, sqlglot_dialect(engine), engine=engine, **kw)

    def to_sql(self, question: str, check_meaning: bool = False) -> SQLAnswer:
        system = NL2SQL_SYSTEM.format(
            dialect=self.dialect, schema=self._schema_text, business_rules=render_rules(self.rules)
        )
        messages: list[dict] = [{"role": "user", "content": question}]
        errors: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            out = self.llm.json(system, messages, NL2SQL_SCHEMA)
            if out.get("needs_clarification"):
                return SQLAnswer(None, self.dialect, out.get("assumptions", []),
                                 out.get("clarification_question") or "Can you be more specific?",
                                 attempt, errors)

            sql = (out.get("sql") or "").strip().rstrip(";")
            problems = self._validate(sql)
            if not problems:
                checked = safety.check(sql, self.schema, self.dialect, self.max_rows)
                ans = SQLAnswer(checked.sql, self.dialect, out.get("assumptions", []), None, attempt, errors)
                if check_meaning:
                    ans.meaning = self.to_nl(checked.sql).summary
                return ans

            errors.extend(problems)
            # Feed the concrete failure back. The model fixes its own mistakes well when told exactly what broke.
            messages += [
                {"role": "assistant", "content": json.dumps(out)},
                {"role": "user", "content": "That query was rejected:\n- " + "\n- ".join(problems)
                 + "\nReturn a corrected query for the original question."},
            ]

        return SQLAnswer(None, self.dialect, [], None, self.max_attempts, errors)

    def _validate(self, sql: str) -> list[str]:
        if not sql:
            return ["Empty SQL."]
        checked = safety.check(sql, self.schema, self.dialect, self.max_rows)
        if not checked.ok:
            return checked.errors
        if self.engine is not None:
            err = executor.explain(self.engine, checked.sql)
            if err:
                return [f"Database rejected the query plan: {err}"]
        return []

    # ------------------------------------------------------------ SQL -> NL

    def to_nl(self, sql: str, audience: str = "a non-technical business user") -> "Explanation":
        return explain_sql(self.llm, sql, self.dialect, self.schema, audience, self.rules)


# ---------------------------------------------------------------- SQL -> NL

NL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "steps", "warnings"],
    "additionalProperties": False,
}

NL_SYSTEM = """You explain SQL queries in plain language for {audience}.

{schema_block}{business_rules}
Facts extracted by a SQL parser (trust these over your own reading):
{facts}

Write:
- summary: one or two sentences saying what question this query answers and what each output row represents. No SQL jargon.
- steps: a short ordered list of what the query does (which data it starts from, how it filters, joins, groups, sorts, limits).
- warnings: real problems a careful reviewer would flag, for example a write with no WHERE clause, a LEFT JOIN whose WHERE filter turns it into an inner join, integer division, a non-deterministic LIMIT without ORDER BY, comparing to NULL with =, possible row duplication from a join, or names not in the schema, or a query that breaks one of the business rules above (say which rule). Empty list if none."""


@dataclass
class Explanation:
    summary: str
    steps: list[str]
    warnings: list[str]
    facts: dict


def sql_facts(tree: exp.Expression, schema: Schema | None, dialect: str) -> dict:
    """Deterministic structure of the query. Grounds the explanation and feeds warnings."""
    facts: dict = {"statement": type(tree).__name__}
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    facts["tables"] = sorted({t.name for t in tree.find_all(exp.Table) if t.name and t.name not in ctes})
    if ctes:
        facts["ctes"] = sorted(ctes)
    joins = [f"{(j.args.get('side') or '')} {(j.args.get('kind') or '')} JOIN {j.this.sql(dialect)}".split()
             for j in tree.find_all(exp.Join)]
    if joins:
        facts["joins"] = [" ".join(j) for j in joins]
    if (where := tree.find(exp.Where)) is not None:
        facts["where"] = where.this.sql(dialect)
    if (group := tree.find(exp.Group)) is not None:
        facts["group_by"] = [g.sql(dialect) for g in group.expressions]
    if (order := tree.find(exp.Order)) is not None:
        facts["order_by"] = [o.sql(dialect) for o in order.expressions]
    if (limit := tree.args.get("limit")) is not None:
        facts["limit"] = limit.expression.sql(dialect)
    if isinstance(tree, exp.Select):
        facts["outputs"] = [s.alias_or_name or s.sql(dialect) for s in tree.expressions]

    warnings = []
    ro = safety.check_read_only(tree)
    if ro:
        warnings += ro
    if isinstance(tree, (exp.Update, exp.Delete)) and tree.args.get("where") is None:
        warnings.append(f"{type(tree).__name__.upper()} without WHERE affects every row.")
    if schema is not None and isinstance(tree, exp.Query):
        schema_errors, _ = safety.check_against_schema(tree, schema, dialect)
        warnings += schema_errors
    if warnings:
        facts["parser_warnings"] = warnings
    return facts


def explain_sql(
    llm: LLM,
    sql: str,
    dialect: str,
    schema: Schema | None = None,
    audience: str = "a non-technical business user",
    rules: list[str] | None = None,
) -> Explanation:
    tree = safety.parse_one(sql, dialect)  # raises on invalid SQL; explaining garbage helps no one
    facts = sql_facts(tree, schema, dialect)
    schema_block = f"Database schema:\n{schema.render()}\n" if schema else "No schema was provided.\n"
    system = NL_SYSTEM.format(
        audience=audience,
        schema_block=schema_block,
        business_rules=render_rules(rules or []),
        facts=json.dumps(facts, indent=2, default=str),
    )
    out = llm.json(system, [{"role": "user", "content": f"Explain this {dialect} query:\n\n{sql}"}], NL_SCHEMA)
    warnings = list(dict.fromkeys(facts.get("parser_warnings", []) + out.get("warnings", [])))
    return Explanation(out["summary"], out.get("steps", []), warnings, facts)


# ---------------------------------------------------------------- result -> plain language

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

ANSWER_SYSTEM = """You answer a user's question in plain language, using ONLY the query result below.

Question: {question}

SQL that produced the result:
{sql}

Result ({shown} of {total} rows, columns: {columns}):
{rows}

Write `answer`: one to three short sentences that directly answer the question for a non-technical reader.
- Lead with the answer itself (the name, the number, the trend), not with a description of the data.
- Copy every number exactly as it appears in the result. You may shorten a long number only by rounding it and keeping its unit (for example 73906449.55 -> about 73.9 million). Do not calculate new numbers such as percentages, totals or differences.
- If the result is empty, say that nothing matched. If only some rows are shown, do not claim anything about the rows you cannot see.
- No SQL words, no column names in snake_case, no markdown."""


@dataclass
class ResultAnswer:
    text: str
    unverified: list[str]  # numbers in the text that are not in the result, the question or the SQL


_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?:\s*(?:%|percent|k\b|thousand|m\b|mn\b|million|b\b|bn\b|billion|lakh|crore))?", re.I)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
          "lakh": 1e5, "crore": 1e7}


def _numbers_in(values) -> list[float]:
    out = []
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out.append(float(v))
            continue
        for m in re.finditer(r"-?\d+(?:\.\d+)?", str(v).replace(",", "")):
            out.append(float(m.group()))
    return out


def unverified_numbers(text: str, allowed: list[float]) -> list[str]:
    """Numbers in `text` that don't match any allowed value, allowing rounding and 'million'-style units.

    This is a cheap guard against a model inventing figures. It can't judge meaning, only digits.
    """
    bad = []
    for m in _NUM.finditer(text):
        raw = m.group().strip()
        num_part = re.match(r"-?[\d,]*\d(?:\.\d+)?", raw).group()
        unit = raw[len(num_part):].strip().lower()
        try:
            n = float(num_part.replace(",", ""))
        except ValueError:
            continue
        decimals = len(num_part.split(".")[1]) if "." in num_part else 0
        scale = _SCALE.get(unit, 1.0)
        tol = 0.5 * 10 ** -decimals * scale  # "73.9 million" covers 73.85M..73.95M
        if unit in ("%", "percent"):
            tol, scale = 0.5 * 10 ** -decimals, 1.0
        target = n * scale
        if not any(abs(a - target) <= tol + 1e-9 or abs(abs(a) - abs(target)) <= tol + 1e-9 for a in allowed):
            bad.append(raw)
    return bad


def answer_in_words(llm: LLM, question: str, sql: str, columns: list[str], rows: list, max_rows: int = 50) -> ResultAnswer:
    shown = rows[:max_rows]
    table = "\n".join(" | ".join("NULL" if v is None else str(v) for v in r) for r in shown) or "(no rows)"
    system = ANSWER_SYSTEM.format(question=question, sql=sql, shown=len(shown), total=len(rows),
                                  columns=", ".join(columns), rows=table)
    out = llm.json(system, [{"role": "user", "content": question}], ANSWER_SCHEMA)
    text = (out.get("answer") or "").strip()
    allowed = _numbers_in(v for r in shown for v in r) + _numbers_in([question, sql]) + [float(len(rows)), float(len(shown))]
    return ResultAnswer(text, unverified_numbers(text, allowed))
