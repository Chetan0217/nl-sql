"""Read a live database's schema and render it for the model and the validator."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine, inspect, select, table, column, text


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    samples: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


@dataclass
class Table:
    name: str
    columns: list[Column]
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    comment: str | None = None


@dataclass
class Schema:
    tables: list[Table]

    def table(self, name: str) -> Table | None:
        lname = name.lower()
        return next((t for t in self.tables if t.name.lower() == lname), None)

    def as_sqlglot_mapping(self) -> dict[str, dict[str, str]]:
        """{table: {column: type}} — the shape sqlglot's qualifier expects."""
        return {t.name: {c.name: c.type for c in t.columns} for t in self.tables}

    def render(self) -> str:
        """Compact DDL-like text. This is what grounds the model in the real schema."""
        out = []
        for t in self.tables:
            lines = []
            for c in t.columns:
                bits = [c.name, c.type]
                if c.primary_key:
                    bits.append("PRIMARY KEY")
                if not c.nullable and not c.primary_key:
                    bits.append("NOT NULL")
                line = "  " + " ".join(bits)
                if c.samples:
                    line += "  -- e.g. " + ", ".join(repr(s) for s in c.samples)
                lines.append(line)
            for fk in t.foreign_keys:
                lines.append(
                    f"  FOREIGN KEY ({', '.join(fk.columns)}) REFERENCES "
                    f"{fk.ref_table}({', '.join(fk.ref_columns)})"
                )
            header = f"TABLE {t.name}"
            if t.comment:
                header += f"  -- {t.comment}"
            out.append(header + " (\n" + ",\n".join(lines) + "\n)")
        return "\n\n".join(out)


def _is_texty(type_name: str) -> bool:
    t = type_name.upper()
    return any(k in t for k in ("CHAR", "TEXT", "STRING", "ENUM", "VARCHAR"))


def introspect(
    engine: Engine,
    schema_name: str | None = None,
    include: list[str] | None = None,
    sample_values: int = 3,
) -> Schema:
    """Introspect tables, columns, keys and a few distinct sample values for text columns.

    Sample values help the model match "customers in Germany" to country = 'DE'.
    Set sample_values=0 if column contents are sensitive and must not reach the model.
    """
    insp = inspect(engine)
    names = insp.get_table_names(schema=schema_name)
    if include:
        wanted = {n.lower() for n in include}
        names = [n for n in names if n.lower() in wanted]

    tables: list[Table] = []
    for name in sorted(names):
        pk = set(insp.get_pk_constraint(name, schema=schema_name).get("constrained_columns") or [])
        cols = [
            Column(
                name=c["name"],
                type=str(c["type"]),
                nullable=bool(c.get("nullable", True)),
                primary_key=c["name"] in pk,
            )
            for c in insp.get_columns(name, schema=schema_name)
        ]
        fks = [
            ForeignKey(fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
            for fk in insp.get_foreign_keys(name, schema=schema_name)
        ]
        try:
            comment = insp.get_table_comment(name, schema=schema_name).get("text")
        except NotImplementedError:
            comment = None
        tables.append(Table(name, cols, fks, comment))

    if sample_values > 0:
        with engine.connect() as conn:
            for t in tables:
                for c in t.columns:
                    if not _is_texty(c.type) or c.primary_key:
                        continue
                    tbl = table(t.name, column(c.name), schema=schema_name)
                    q = select(tbl.c[c.name]).where(tbl.c[c.name].is_not(None)).distinct().limit(sample_values)
                    try:
                        c.samples = [str(r[0])[:40] for r in conn.execute(q)]
                    except Exception:
                        conn.rollback()  # e.g. no SELECT grant on this table; skip samples
    return Schema(tables)
