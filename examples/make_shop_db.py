"""Create a small sample shop database (SQLite by default, or any SQLAlchemy URL)."""

import sys

from sqlalchemy import create_engine, text

DDL = [
    "CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, country VARCHAR(2) NOT NULL, created_at DATE NOT NULL)",
    "CREATE TABLE products (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, category VARCHAR(50) NOT NULL, unit_price NUMERIC(10,2) NOT NULL)",
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), ordered_at DATE NOT NULL, status VARCHAR(20) NOT NULL)",
    "CREATE TABLE order_items (order_id INTEGER NOT NULL REFERENCES orders(id), product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL, PRIMARY KEY (order_id, product_id))",
]
DATA = [
    "INSERT INTO customers VALUES (1,'Asha Rao','IN','2025-01-10'),(2,'Ben Ott','DE','2025-02-03'),(3,'Carla Diaz','US','2025-03-15'),(4,'Dev Mehta','IN','2025-06-01')",
    "INSERT INTO products VALUES (1,'Laptop','electronics',900.00),(2,'Mouse','electronics',20.00),(3,'Desk','furniture',250.00),(4,'Chair','furniture',120.00)",
    "INSERT INTO orders VALUES (1,1,'2025-07-01','shipped'),(2,1,'2025-08-12','shipped'),(3,2,'2025-08-20','cancelled'),(4,3,'2025-09-05','shipped'),(5,4,'2025-09-09','pending')",
    "INSERT INTO order_items VALUES (1,1,1),(1,2,2),(2,4,4),(3,3,1),(4,1,2),(4,3,1),(5,2,5)",
]

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "sqlite:///shop.db"
    with create_engine(url).begin() as conn:
        for s in ["DROP TABLE IF EXISTS order_items", "DROP TABLE IF EXISTS orders",
                  "DROP TABLE IF EXISTS products", "DROP TABLE IF EXISTS customers"] + DDL + DATA:
            conn.execute(text(s))
    print(f"Created sample shop database at {url}")
