"""Command line: nlsql ask | explain | transpile | schema | dialects."""

from __future__ import annotations

import argparse
import os
import sys

from . import executor, safety
from .rules import load_rules
from .schema import introspect
from .translator import Translator, explain_sql, sqlglot_dialect


def _print_table(result: executor.QueryResult, max_rows: int = 50) -> None:
    cols = result.columns
    rows = [[("" if v is None else str(v)) for v in r] for r in result.rows[:max_rows]]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print(" | ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(v.ljust(w) for v, w in zip(r, widths)))
    if len(result.rows) > max_rows:
        print(f"... {len(result.rows) - max_rows} more rows")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nlsql", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="Plain-language question -> SQL (optionally run it)")
    a.add_argument("question")
    a.add_argument("--db", required=True, help="SQLAlchemy URL, e.g. sqlite:///shop.db or postgresql+psycopg://user@host/db")
    a.add_argument("--run", action="store_true", help="Execute the checked query read-only and print rows")
    a.add_argument("--check-meaning", action="store_true", help="Independently explain the generated SQL back in plain language")
    a.add_argument("--max-rows", type=int, default=1000)
    a.add_argument("--tables", nargs="*", help="Only expose these tables to the model")
    a.add_argument("--no-samples", action="store_true", help="Do not send sample column values to the model")
    a.add_argument("--rules", default=os.environ.get("NLSQL_RULES"), help="Business rules file (one rule per line)")

    e = sub.add_parser("explain", help="SQL -> plain language")
    e.add_argument("sql", help="SQL text, or - to read stdin")
    e.add_argument("--dialect", help="sqlglot dialect (default: from --db, else 'postgres')")
    e.add_argument("--db", help="Optional: ground the explanation in this database's schema")
    e.add_argument("--audience", default="a non-technical business user")
    e.add_argument("--rules", default=os.environ.get("NLSQL_RULES"), help="Business rules file (one rule per line)")

    t = sub.add_parser("transpile", help="Convert SQL between dialects (no model call)")
    t.add_argument("sql")
    t.add_argument("--from", dest="read", required=True)
    t.add_argument("--to", dest="write", required=True)

    s = sub.add_parser("schema", help="Show the schema text the model sees")
    s.add_argument("--db", required=True)

    sub.add_parser("dialects", help="List supported SQL dialects")

    args = p.parse_args(argv)

    if args.cmd == "dialects":
        print("\n".join(safety.SUPPORTED_DIALECTS))
        return 0
    if args.cmd == "transpile":
        print(safety.transpile(args.sql, args.read, args.write))
        return 0

    from .llm import make_llm

    if args.cmd == "schema":
        print(introspect(executor.make_engine(args.db)).render())
        return 0

    if args.cmd == "ask":
        engine = executor.make_engine(args.db)
        schema = introspect(engine, include=args.tables, sample_values=0 if args.no_samples else 3)
        tr = Translator.from_engine(make_llm(), engine, schema, max_rows=args.max_rows, rules=load_rules(args.rules))
        ans = tr.to_sql(args.question, check_meaning=args.check_meaning)
        if ans.clarification:
            print(f"Need clarification: {ans.clarification}")
            return 2
        if not ans.ok:
            print("Could not produce a valid query. Last errors:", *ans.errors[-3:], sep="\n  ", file=sys.stderr)
            return 1
        print(ans.sql)
        for x in ans.assumptions:
            print(f"-- assumption: {x}")
        if ans.meaning:
            print(f"-- this query answers: {ans.meaning}")
        if args.run:
            print()
            _print_table(executor.run(engine, ans.sql))
        return 0

    if args.cmd == "explain":
        sql = sys.stdin.read() if args.sql == "-" else args.sql
        schema, dialect = None, args.dialect
        if args.db:
            engine = executor.make_engine(args.db)
            schema = introspect(engine, sample_values=0)
            dialect = dialect or sqlglot_dialect(engine)
        ex = explain_sql(make_llm(), sql, dialect or "postgres", schema, args.audience, load_rules(args.rules))
        print(ex.summary, "", *[f"{i}. {s}" for i, s in enumerate(ex.steps, 1)], sep="\n")
        if ex.warnings:
            print("\nWarnings:", *[f"  - {w}" for w in ex.warnings], sep="\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
