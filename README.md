# nlsql: plain language ⇄ SQL

Turns a question into a checked, read-only SQL query for your database, and explains any SQL query back in plain language.

## Design

**The language model is the least trustworthy part, so it sits inside deterministic checks.**

```
question ──► schema grounding ──► Claude (structured JSON) ──► static checks ──► EXPLAIN on the real DB ──► run read-only
                 ▲                         ▲                         │                    │
                 │                         └──── exact error fed back, up to 3 tries ◄────┘
           live introspection
     (tables, columns, keys, sample values)

SQL ──► sqlglot parse ──► parser facts + warnings ──► Claude explains, grounded in facts + schema
```

| Layer | What it does | File |
|---|---|---|
| Schema grounding | Reads tables, columns, types, keys and a few sample text values from the live database. The model only ever sees real names. | `nlsql/schema.py` |
| Static safety | Parses with sqlglot in the target dialect. Rejects anything but one read query: writes, DDL, multi-statements, `SELECT INTO`, writes hidden in CTEs, `FOR UPDATE`, and dangerous functions (`pg_sleep`, file reads, `dblink`…). Checks every table and column exists. Caps rows with a LIMIT. | `nlsql/safety.py` |
| Database check | Runs `EXPLAIN` so the database itself catches type errors and dialect mistakes before execution. | `nlsql/executor.py` |
| Read-only execution | Postgres/MySQL: `SET TRANSACTION READ ONLY` plus a statement timeout. SQLite: opened read-only with `query_only`. | `nlsql/executor.py` |
| Self-correction | When a check fails, the exact error goes back to Claude, which retries (3 attempts max). | `nlsql/translator.py` |
| Clarification | Ambiguous questions ("top customers") return a question back instead of a guess. | `nlsql/translator.py` |
| SQL → NL | sqlglot extracts tables, joins, filters, grouping and warnings first; Claude writes the explanation from those facts, so it can't misread the query's structure. Works on writes too (it explains, never runs). | `nlsql/translator.py` |
| Model | Claude Opus 5.5 via the Anthropic API, JSON-schema structured output, prompt caching on the schema, server-side refusal fallback. Override with `NLSQL_MODEL`, `NLSQL_EFFORT`. | `nlsql/llm.py` |
| Free local model | `NLSQL_LLM=ollama` swaps Claude for a model served by [Ollama](https://ollama.com) on your own machine (default `qwen2.5-coder:7b`, override with `NLSQL_OLLAMA_MODEL`, `OLLAMA_HOST`). No API key or cost; expect more wrong-but-valid queries than with Claude. | `nlsql/llm.py` |

### Dialects

sqlglot parses and generates 32 dialects (`nlsql dialects`), including Postgres, MySQL, SQLite, SQL Server (`tsql`), Oracle, Snowflake, BigQuery, Redshift, DuckDB, ClickHouse, Spark, Trino and Teradata. SQLAlchemy connects to most of these with the matching driver.

**Tested end to end:** SQLite, Postgres 16 and MySQL-compatible MariaDB 10.11. On SQLite and MySQL, `EXPLAIN` does not catch type mistakes such as comparing a date to a number, because those databases silently coerce types. **Parse/validate only, not run against a live server:** everything else. Each new database needs its read-only setup and `EXPLAIN` behavior checked in `executor.py` before you trust it with users.

## Run it

```bash
pip install -e ".[postgres,test]"
export ANTHROPIC_API_KEY=...

python examples/make_shop_db.py sqlite:///shop.db           # sample data

nlsql ask "Revenue by country from shipped orders" --db sqlite:///shop.db --run
nlsql ask "Top customers" --db sqlite:///shop.db --check-meaning
nlsql explain "SELECT c.name, COUNT(*) FROM customers c LEFT JOIN orders o ON o.customer_id = c.id WHERE o.status = 'shipped' GROUP BY 1" --db sqlite:///shop.db
nlsql transpile "SELECT TOP 5 * FROM orders" --from tsql --to postgres   # no model call
nlsql schema --db sqlite:///shop.db                                       # what the model sees
```

### Website (local, free)

```bash
pip install -e ".[mysql,web]"
export NLSQL_DB="mysql+pymysql://nlsql_ro:<password>@localhost/nlsql_demo"   # PowerShell: $env:NLSQL_DB = "..."
nlsql-web            # opens http://127.0.0.1:8000, uses Ollama unless NLSQL_LLM=claude
```

On Windows you can instead double-click `start-website.cmd`. The first time it asks for the `nlsql_ro` password and saves it in `local-settings.cmd`, which `.gitignore` keeps out of the repository. Delete that file to change the password.

Four tabs: Question → SQL (SQL, assumptions, optional meaning check, rows, CSV), SQL → Explanation (summary, steps, warnings, parser facts, run read-only), Business rules (edit the rules file), Schema (what the model sees). The server listens on 127.0.0.1 only and refuses other host names. Code: `nlsql/web/`.

### Business rules

The schema can't say "cancelled orders aren't revenue" or "use unit_price, not list_price". Put those in a text file, one rule per line (`examples/business_rules.txt` for `nlsql_demo`), and pass it with `--rules` or `NLSQL_RULES`. Rules go into both the question prompt and the explanation prompt, where breaking one becomes a warning. They are instructions to the model, not checks: a small model can still ignore them. Code: `nlsql/rules.py`.

In Python:

```python
from nlsql import Translator, introspect
from nlsql.executor import make_engine, run
from nlsql.llm import ClaudeLLM

engine = make_engine("postgresql+psycopg://readonly_user:pw@host/db")
tr = Translator.from_engine(ClaudeLLM(), engine, introspect(engine))
ans = tr.to_sql("How many orders shipped last month?")
print(ans.sql if ans.ok else ans.clarification or ans.errors)
print(tr.to_nl(ans.sql).summary)
```

## MySQL with synthetic data

```bash
python examples/synthetic_mysql.py --out retail_demo.sql   # 10 tables, 15,000 orders, same data every run
```

Open `retail_demo.sql` in MySQL Workbench and run it (File > Open SQL Script, then the lightning bolt). It creates the `nlsql_demo` database. Then run `examples/readonly_user.sql` to create a SELECT-only user, and connect as that user:

```bash
pip install -e ".[mysql]"
nlsql ask "Which product category earned the most in 2025?" --db "mysql+pymysql://nlsql_ro:<password>@localhost/nlsql_demo" --run
```

## Tests

```bash
pytest tests                                                          # SQLite, no API key needed
NLSQL_PG_URL=postgresql+psycopg://user:pw@localhost/shop pytest tests # also Postgres
```

A scripted fake replaces Claude, so the tests prove the checks, retries and execution, not the model's accuracy.

## Before real users touch it

1. **Connect with a database user that has only SELECT grants.** The static checks and read-only transactions are defense in depth, not a substitute.
2. **Build an accuracy eval on your own schema.** Collect 50 to 100 real questions with known-correct answers and compare result sets, not SQL text. Without this you can't tell whether the system is right.
3. **Large schemas** (more than ~100 tables) need table retrieval before the prompt; use `--tables` / `include=` for now.
4. **Row-level access**: if different users may see different rows, enforce it in the database (views or row-level security), never in the prompt.
5. `sample_values=0` (`--no-samples`) if column contents must not leave your network.
