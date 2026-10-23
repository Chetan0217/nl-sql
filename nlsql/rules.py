"""Business rules: the facts about your data that the schema alone can't tell the model.

A schema says `orders.status` exists. It does not say "cancelled orders are not
sales". Without that, a model writes SQL that runs fine and gives the wrong number.
Rules live in a plain text file, one rule per line, so a non-programmer can edit them.

File format:
    # lines starting with # are comments
    Revenue means SUM(order_items.quantity * order_items.unit_price).
    Exclude orders with status = 'cancelled' from revenue, sales and order counts.
"""

from __future__ import annotations

from pathlib import Path


def parse_rules(text: str) -> list[str]:
    """Turn the file's text into a clean list: no blanks, no comments, no leading '- '."""
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        rules.append(line)
    return rules


def load_rules(path: str | Path | None) -> list[str]:
    """Read rules from a file. A missing path or file means 'no rules', not an error."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    return parse_rules(p.read_text(encoding="utf-8"))


def render_rules(rules: list[str]) -> str:
    """The block that goes into the model's instructions. Empty string when there are no rules."""
    if not rules:
        return ""
    lines = "\n".join(f"- {r}" for r in rules)
    return (
        "\nBusiness rules for this database (always apply them unless the question explicitly "
        "asks otherwise, and mention each one you applied in assumptions):\n" + lines + "\n"
    )
