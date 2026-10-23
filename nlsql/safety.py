"""Static checks on SQL before it ever touches a database.

Everything here is deterministic. The model is never trusted to police itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from .schema import Schema

# Statement types that change data, schema, permissions or session state.
_WRITE_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, n)
    for n in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
        "TruncateTable", "Grant", "Revoke", "Command", "Set", "Use", "Copy", "Into",
        "LoadData", "Transaction", "Commit", "Rollback", "Pragma", "Attach", "Detach",
    )
    if hasattr(exp, n)
)

# Functions that read files, sleep, or reach outside the database. Lower-cased.
_BLOCKED_FUNCS = {
    "pg_sleep", "sleep", "benchmark", "waitfor", "pg_read_file", "pg_read_binary_file",
    "pg_ls_dir", "lo_import", "lo_export", "load_file", "dblink", "dblink_exec",
    "load_extension", "readfile", "writefile", "xp_cmdshell", "openrowset", "opendatasource",
    "pg_terminate_backend", "pg_cancel_backend", "set_config", "current_setting",
    "utl_http.request", "dbms_pipe.receive_message",
}

SUPPORTED_DIALECTS = sorted(d.value for d in sqlglot.Dialects if d.value)


class UnsafeSQL(ValueError):
    pass


@dataclass
class CheckResult:
    ok: bool
    sql: str  # normalized, possibly with LIMIT added
    errors: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)


def parse_one(sql: str, dialect: str) -> exp.Expression:
    statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    if len(statements) != 1:
        raise UnsafeSQL(f"Expected exactly one statement, got {len(statements)}.")
    tree = statements[0]
    # Lenient dialects parse typos like "SELEC * FORM x" as a bare expression. That is not a statement.
    if isinstance(tree, (exp.Alias, exp.Column, exp.Identifier, exp.Literal, exp.Binary, exp.Paren, exp.Star)):
        raise UnsafeSQL("This is not a SQL statement (check the first keyword for typos).")
    return tree


def check_read_only(tree: exp.Expression) -> list[str]:
    errors = []
    if not isinstance(tree, exp.Query):
        errors.append(f"Only read queries are allowed; got {type(tree).__name__}.")
    for node in tree.walk():
        if isinstance(node, _WRITE_NODES):
            errors.append(f"Write or DDL operation not allowed: {type(node).__name__}.")
            break
    for fn in tree.find_all(exp.Func):
        name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if name in _BLOCKED_FUNCS:
            errors.append(f"Function not allowed: {name}.")
    # SELECT ... FOR UPDATE takes locks.
    if tree.find(exp.Lock):
        errors.append("Locking clauses (FOR UPDATE/SHARE) are not allowed.")
    return errors


def check_against_schema(tree: exp.Expression, schema: Schema, dialect: str) -> tuple[list[str], list[str]]:
    """Every table and column must exist. Catches the most common hallucinations."""
    errors: list[str] = []
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    used = []
    for t in tree.find_all(exp.Table):
        if not t.name or t.name.lower() in cte_names:
            continue
        used.append(t.name)
        if schema.table(t.name) is None:
            errors.append(f"Unknown table: {t.name}.")
    if errors:
        return errors, used
    try:
        qualify(
            tree.copy(),
            schema=schema.as_sqlglot_mapping(),
            dialect=dialect,
            validate_qualify_columns=True,
            identify=False,
        )
    except OptimizeError as e:
        errors.append(f"Column check failed: {e}")
    return errors, sorted(set(used))


def enforce_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    """Cap rows returned. Keeps an existing smaller LIMIT."""
    if not isinstance(tree, exp.Query):
        return tree
    existing = tree.args.get("limit")
    if existing is not None:
        try:
            if int(existing.expression.name) <= max_rows:
                return tree
        except (AttributeError, ValueError):
            pass
    return tree.limit(max_rows, copy=True)


def check(sql: str, schema: Schema | None, dialect: str, max_rows: int | None = 1000) -> CheckResult:
    try:
        tree = parse_one(sql, dialect)
    except (ParseError, UnsafeSQL) as e:
        return CheckResult(False, sql, [f"Parse error ({dialect}): {e}"])
    errors = check_read_only(tree)
    tables: list[str] = []
    if not errors and schema is not None:
        schema_errors, tables = check_against_schema(tree, schema, dialect)
        errors += schema_errors
    if errors:
        return CheckResult(False, sql, errors, tables)
    if max_rows:
        tree = enforce_limit(tree, max_rows)
    return CheckResult(True, tree.sql(dialect=dialect, pretty=True), [], tables)


def transpile(sql: str, read: str, write: str) -> str:
    tree = parse_one(sql, read)
    return tree.sql(dialect=write, pretty=True)
