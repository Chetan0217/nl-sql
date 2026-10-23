"""Generate a synthetic retail database for MySQL (8.0+) or MariaDB.

Standard library only. Same seed -> same data, so test answers stay stable.

  python synthetic_mysql.py --out retail_demo.sql                 # script to open in MySQL Workbench
  python synthetic_mysql.py --url mysql+pymysql://root:pw@localhost/  # load directly (needs pymysql + sqlalchemy)
"""

from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta

DB = "nlsql_demo"

DDL = """
CREATE TABLE regions (
  id INT PRIMARY KEY,
  name VARCHAR(40) NOT NULL
) COMMENT='Sales regions';

CREATE TABLE stores (
  id INT PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  city VARCHAR(60) NOT NULL,
  region_id INT NOT NULL,
  opened_on DATE NOT NULL,
  FOREIGN KEY (region_id) REFERENCES regions(id)
) COMMENT='Physical stores; online orders have no store';

CREATE TABLE employees (
  id INT PRIMARY KEY,
  full_name VARCHAR(80) NOT NULL,
  role VARCHAR(30) NOT NULL,
  store_id INT NULL,
  manager_id INT NULL,
  hired_on DATE NOT NULL,
  salary DECIMAL(10,2) NOT NULL,
  FOREIGN KEY (store_id) REFERENCES stores(id),
  FOREIGN KEY (manager_id) REFERENCES employees(id)
);

CREATE TABLE customers (
  id INT PRIMARY KEY,
  first_name VARCHAR(40) NOT NULL,
  last_name VARCHAR(40) NOT NULL,
  email VARCHAR(120) NOT NULL UNIQUE,
  city VARCHAR(60) NOT NULL,
  segment ENUM('consumer','small_business','enterprise') NOT NULL,
  signup_date DATE NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1
);

CREATE TABLE categories (
  id INT PRIMARY KEY,
  name VARCHAR(60) NOT NULL,
  parent_id INT NULL,
  FOREIGN KEY (parent_id) REFERENCES categories(id)
) COMMENT='Two-level product category tree';

CREATE TABLE products (
  id INT PRIMARY KEY,
  sku VARCHAR(20) NOT NULL UNIQUE,
  name VARCHAR(100) NOT NULL,
  category_id INT NOT NULL,
  list_price DECIMAL(10,2) NOT NULL,
  unit_cost DECIMAL(10,2) NOT NULL,
  discontinued TINYINT(1) NOT NULL DEFAULT 0,
  FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE orders (
  id INT PRIMARY KEY,
  customer_id INT NOT NULL,
  store_id INT NULL,
  employee_id INT NULL,
  channel ENUM('online','in_store','phone') NOT NULL,
  status ENUM('pending','paid','shipped','delivered','cancelled') NOT NULL,
  ordered_at DATETIME NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id),
  FOREIGN KEY (store_id) REFERENCES stores(id),
  FOREIGN KEY (employee_id) REFERENCES employees(id)
);

CREATE TABLE order_items (
  id INT PRIMARY KEY,
  order_id INT NOT NULL,
  product_id INT NOT NULL,
  quantity INT NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL COMMENT 'Price actually charged, after discount',
  discount_pct DECIMAL(5,2) NOT NULL DEFAULT 0,
  FOREIGN KEY (order_id) REFERENCES orders(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE payments (
  id INT PRIMARY KEY,
  order_id INT NOT NULL,
  method ENUM('card','upi','cash','bank_transfer','wallet') NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  paid_at DATETIME NOT NULL,
  FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE returns (
  id INT PRIMARY KEY,
  order_item_id INT NOT NULL,
  reason VARCHAR(40) NOT NULL,
  returned_at DATE NOT NULL,
  refund_amount DECIMAL(10,2) NOT NULL,
  FOREIGN KEY (order_item_id) REFERENCES order_items(id)
);
"""

TABLES_IN_DROP_ORDER = ["returns", "payments", "order_items", "orders", "products",
                        "categories", "customers", "employees", "stores", "regions"]

FIRST = ["Aarav", "Diya", "Rohan", "Isha", "Kabir", "Ananya", "Vikram", "Meera", "Arjun", "Priya", "Sanjay",
         "Neha", "Rahul", "Kavya", "Aditya", "Sneha", "Liam", "Emma", "Noah", "Olivia", "Mateo", "Sofia",
         "Yuki", "Hana", "Omar", "Layla", "Lukas", "Mia", "Chen", "Wei"]
LAST = ["Sharma", "Patel", "Mehta", "Iyer", "Reddy", "Nair", "Gupta", "Singh", "Kapoor", "Joshi", "Rao",
        "Das", "Smith", "Garcia", "Muller", "Rossi", "Tanaka", "Khan", "Lopez", "Brown", "Wang", "Kim"]
REGIONS = ["North", "South", "East", "West", "Central"]
CITIES = {"North": ["Delhi", "Chandigarh", "Jaipur"], "South": ["Bengaluru", "Chennai", "Hyderabad"],
          "East": ["Kolkata", "Bhubaneswar"], "West": ["Mumbai", "Pune", "Ahmedabad"], "Central": ["Indore", "Bhopal"]}
CATS = {"Electronics": ["Laptops", "Phones", "Audio", "Accessories"],
        "Home": ["Furniture", "Kitchen", "Decor"],
        "Fashion": ["Men", "Women", "Footwear"],
        "Sports": ["Fitness", "Outdoor"],
        "Books": ["Fiction", "Non-fiction", "Education"]}
PRICE = {"Laptops": (35000, 120000), "Phones": (8000, 90000), "Audio": (800, 25000), "Accessories": (200, 4000),
         "Furniture": (3000, 45000), "Kitchen": (300, 9000), "Decor": (200, 6000), "Men": (400, 6000),
         "Women": (400, 8000), "Footwear": (900, 12000), "Fitness": (500, 30000), "Outdoor": (700, 20000),
         "Fiction": (150, 900), "Non-fiction": (200, 1500), "Education": (300, 2500)}
ADJ = ["Pro", "Lite", "Max", "Classic", "Eco", "Prime", "Ultra", "Smart", "Mini", "Plus"]
RETURN_REASONS = ["damaged", "wrong_size", "not_as_described", "changed_mind", "late_delivery"]


def q(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(round(v, 2)) if isinstance(v, float) else str(v)
    if isinstance(v, (date, datetime)):
        return f"'{v.isoformat(sep=' ') if isinstance(v, datetime) else v.isoformat()}'"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def generate(customers=2000, products=150, orders=15000, seed=42) -> dict[str, tuple[list[str], list[tuple]]]:
    rnd = random.Random(seed)
    start, end = date(2024, 1, 1), date(2026, 9, 30)
    span = (end - start).days
    data: dict[str, tuple[list[str], list[tuple]]] = {}

    data["regions"] = (["id", "name"], [(i + 1, r) for i, r in enumerate(REGIONS)])

    stores = []
    for region_id, region in enumerate(REGIONS, 1):
        for city in CITIES[region]:
            sid = len(stores) + 1
            stores.append((sid, f"{city} Central Store", city, region_id, start - timedelta(days=rnd.randint(100, 2000))))
    data["stores"] = (["id", "name", "city", "region_id", "opened_on"], stores)

    emps = [(1, "Nisha Kulkarni", "ceo", None, None, date(2018, 4, 1), 450000.0)]
    for s in stores:
        mgr = len(emps) + 1
        emps.append((mgr, f"{rnd.choice(FIRST)} {rnd.choice(LAST)}", "store_manager", s[0], 1,
                     s[4] + timedelta(days=rnd.randint(0, 200)), float(rnd.randint(90000, 150000))))
        for _ in range(rnd.randint(3, 6)):
            emps.append((len(emps) + 1, f"{rnd.choice(FIRST)} {rnd.choice(LAST)}", "sales_associate", s[0], mgr,
                         s[4] + timedelta(days=rnd.randint(30, 900)), float(rnd.randint(25000, 55000))))
    for _ in range(6):
        emps.append((len(emps) + 1, f"{rnd.choice(FIRST)} {rnd.choice(LAST)}", "phone_agent", None, 1,
                     date(2023, 1, 1) + timedelta(days=rnd.randint(0, 600)), float(rnd.randint(28000, 45000))))
    data["employees"] = (["id", "full_name", "role", "store_id", "manager_id", "hired_on", "salary"], emps)
    associates = {s[0]: [e[0] for e in emps if e[3] == s[0]] for s in stores}
    phone_agents = [e[0] for e in emps if e[2] == "phone_agent"]

    custs, emails = [], set()
    all_cities = [c for cs in CITIES.values() for c in cs]
    for i in range(1, customers + 1):
        fn, ln = rnd.choice(FIRST), rnd.choice(LAST)
        email = f"{fn}.{ln}{i}@example.com".lower()
        emails.add(email)
        seg = rnd.choices(["consumer", "small_business", "enterprise"], [80, 15, 5])[0]
        custs.append((i, fn, ln, email, rnd.choice(all_cities), seg,
                      start + timedelta(days=int(rnd.betavariate(1.2, 2) * span)), rnd.random() > 0.07))
    data["customers"] = (["id", "first_name", "last_name", "email", "city", "segment", "signup_date", "is_active"], custs)

    cats, leaf = [], []
    for parent, children in CATS.items():
        pid = len(cats) + 1
        cats.append((pid, parent, None))
        for ch in children:
            cats.append((len(cats) + 1, ch, pid))
            leaf.append((len(cats), ch))
    data["categories"] = (["id", "name", "parent_id"], cats)

    prods = []
    for i in range(1, products + 1):
        cid, cname = rnd.choice(leaf)
        lo, hi = PRICE[cname]
        price = round(rnd.uniform(lo, hi), -1) - 1
        prods.append((i, f"SKU-{i:05d}", f"{cname.rstrip('s')} {rnd.choice(ADJ)} {rnd.randint(1, 9)}", cid,
                      price, round(price * rnd.uniform(0.45, 0.8), 2), rnd.random() < 0.08))
    data["products"] = (["id", "sku", "name", "category_id", "list_price", "unit_cost", "discontinued"], prods)
    # Popularity is skewed so "best sellers" has a clear answer.
    weights = [1 / (r + 1) ** 0.8 for r in range(products)]
    rnd.shuffle(weights)

    ords, items, pays, rets = [], [], [], []
    for oid in range(1, orders + 1):
        c = rnd.choice(custs)
        # Seasonality: more orders in Oct-Dec.
        while True:
            d = c[6] + timedelta(days=rnd.randint(0, max(1, (end - c[6]).days)))
            if d <= end and (d.month >= 10 or rnd.random() < 0.7):
                break
        ts = datetime(d.year, d.month, d.day, rnd.randint(8, 22), rnd.randint(0, 59), rnd.randint(0, 59))
        channel = rnd.choices(["online", "in_store", "phone"], [60, 32, 8])[0]
        store_id = emp_id = None
        if channel == "in_store":
            store_id = rnd.choice(stores)[0]
            emp_id = rnd.choice(associates[store_id])
        elif channel == "phone":
            emp_id = rnd.choice(phone_agents)
        age = (end - d).days
        status = ("cancelled" if rnd.random() < 0.06 else
                  "pending" if age < 3 else "paid" if age < 6 else "shipped" if age < 12 else "delivered")
        ords.append((oid, c[0], store_id, emp_id, channel, status, ts))

        total = 0.0
        n_lines = rnd.choices([1, 2, 3, 4, 5], [45, 25, 15, 10, 5])[0]
        for pid in sorted(set(rnd.choices(range(1, products + 1), weights, k=n_lines))):
            p = prods[pid - 1]
            disc = rnd.choice([0, 0, 0, 5, 10, 15, 20]) if c[5] == "consumer" else rnd.choice([5, 10, 15, 20, 25])
            unit = round(p[4] * (1 - disc / 100), 2)
            qty = rnd.choices([1, 2, 3, 5, 10], [70, 15, 8, 4, 3] if c[5] != "enterprise" else [20, 20, 20, 20, 20])[0]
            iid = len(items) + 1
            items.append((iid, oid, pid, qty, unit, float(disc)))
            total += unit * qty
            if status == "delivered" and rnd.random() < 0.05:
                rets.append((len(rets) + 1, iid, rnd.choice(RETURN_REASONS),
                             d + timedelta(days=rnd.randint(5, 30)), round(unit * qty, 2)))
        if status != "pending" and status != "cancelled":
            method = rnd.choices(["card", "upi", "cash", "bank_transfer", "wallet"],
                                 [35, 35, 10 if channel == "in_store" else 0, 10 if c[5] != "consumer" else 2, 10])[0]
            pays.append((len(pays) + 1, oid, method, round(total, 2), ts + timedelta(minutes=rnd.randint(0, 120))))

    data["orders"] = (["id", "customer_id", "store_id", "employee_id", "channel", "status", "ordered_at"], ords)
    data["order_items"] = (["id", "order_id", "product_id", "quantity", "unit_price", "discount_pct"], items)
    data["payments"] = (["id", "order_id", "method", "amount", "paid_at"], pays)
    data["returns"] = (["id", "order_item_id", "reason", "returned_at", "refund_amount"], rets)
    return data


LOAD_ORDER = ["regions", "stores", "employees", "customers", "categories", "products",
              "orders", "order_items", "payments", "returns"]


def to_sql(data, batch=500) -> str:
    out = [f"-- Synthetic retail data for nlsql. Safe to drop: DROP DATABASE {DB};",
           f"CREATE DATABASE IF NOT EXISTS {DB} CHARACTER SET utf8mb4;", f"USE {DB};",
           "SET FOREIGN_KEY_CHECKS = 0;"]
    out += [f"DROP TABLE IF EXISTS {t};" for t in TABLES_IN_DROP_ORDER]
    out += ["SET FOREIGN_KEY_CHECKS = 1;", DDL.strip()]
    for t in LOAD_ORDER:
        cols, rows = data[t]
        for i in range(0, len(rows), batch):
            vals = ",\n".join("(" + ",".join(q(v) for v in r) + ")" for r in rows[i:i + batch])
            out.append(f"INSERT INTO {t} ({', '.join(cols)}) VALUES\n{vals};")
    return "\n\n".join(out) + "\n"


READONLY_USER_SQL = f"""-- Run once as an admin. The translator should connect as this user, never as root.
CREATE USER IF NOT EXISTS 'nlsql_ro'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT, SHOW VIEW ON {DB}.* TO 'nlsql_ro'@'localhost';
FLUSH PRIVILEGES;
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="Write a .sql script (open it in MySQL Workbench and run it)")
    ap.add_argument("--url", help="Load directly, e.g. mysql+pymysql://root:pw@localhost/")
    ap.add_argument("--customers", type=int, default=2000)
    ap.add_argument("--products", type=int, default=150)
    ap.add_argument("--orders", type=int, default=15000)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    if not (a.out or a.url):
        ap.error("give --out and/or --url")

    data = generate(a.customers, a.products, a.orders, a.seed)
    script = to_sql(data)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(script)
        print(f"Wrote {a.out}")
    if a.url:
        import pymysql

        from sqlalchemy.engine import make_url

        u = make_url(a.url)
        conn = pymysql.connect(host=u.host or "localhost", port=u.port or 3306, user=u.username,
                               password=u.password or "")
        with conn.cursor() as cur:
            for stmt in script.split(";\n"):
                body = "\n".join(l for l in stmt.splitlines() if not l.startswith("--")).strip()
                if body:
                    cur.execute(body)
        conn.commit()
        conn.close()
        print(f"Loaded database {DB} at {u.host}")
    for t in LOAD_ORDER:
        print(f"  {t:12s} {len(data[t][1]):>6} rows")


if __name__ == "__main__":
    main()
