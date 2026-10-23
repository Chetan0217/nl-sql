# Run nlsql on a new Windows PC

This guide sets up the project from zero on a Windows PC. Each step ends with a check. Do not move on until the check passes.

Commands are for **PowerShell** unless a step says **cmd**. Replace anything in `<angle brackets>`.

You will need about 30 to 45 minutes. The slow part is MySQL.

---

## 0. Decide what you need

| Goal | Steps you need |
|---|---|
| Try it quickly on a small sample database | 1, 2, 3, 4, 8, 9 (SQLite, no MySQL) |
| Run it against the MySQL retail demo (what the first PC has) | All steps |

SQLite needs no database server, so do steps 1 to 4 first even if you want MySQL. If those work, the Python side is fine and any later problem is MySQL.

---

## 1. Get the code

There is no git repository yet, so the code comes as a zip.

1. Download **`nl-sql-bundle.zip`** (attached in the project thread, also in the project's files).
2. Unzip it into a folder with no spaces in the path, for example `C:\code`. The zip already contains an `nl-sql` folder, so you end up with `C:\code\nl-sql`.
3. Open PowerShell in that folder:

```powershell
cd C:\code\nl-sql
dir
```

**Check:** you see `README.md`, `pyproject.toml`, and the folders `nlsql`, `examples`, `tests`.

---

## 2. Install Python

1. Install Python **3.12 or 3.13** from https://www.python.org/downloads/windows/ (3.10+ works; the first PC runs 3.14).
2. On the first installer screen, tick **"Add python.exe to PATH"**. Missing this is the most common failure.
3. Close and reopen PowerShell, then:

```powershell
python --version
```

**Check:** it prints `Python 3.12.x` or newer. If it opens the Microsoft Store instead, Python is not on PATH: re-run the installer and choose "Modify", or turn off the "python.exe" alias under Settings > Apps > Advanced app settings > App execution aliases.

---

## 3. Create a virtual environment and install the package

```powershell
cd C:\code\nl-sql
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[mysql,test]"
```

If `Activate.ps1` fails with "running scripts is disabled", run this once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Every new PowerShell window needs `.\.venv\Scripts\Activate.ps1` again before using `nlsql` or `pytest`. The prompt shows `(.venv)` when it is active.

**Check:**

```powershell
nlsql dialects
```

prints a list of dialects (postgres, mysql, sqlite, tsql, ...).

---

## 4. Run the tests on SQLite (no API key, no MySQL)

```powershell
pytest tests
```

**Check:** everything passes, with some tests **skipped** because Postgres and MySQL are not set up yet. The tests use a scripted fake instead of Claude, so no API key is needed.

---

## 5. Install MySQL Server 8.4

Pick **one** route.

### Route A: you have admin rights (simplest)

1. Download **MySQL Installer / MySQL 8.4 LTS (MSI)** from https://dev.mysql.com/downloads/mysql/ (choose 8.4 LTS, Windows, MSI).
   **First**, install the Microsoft Visual C++ Redistributable (x64) from https://aka.ms/vs/17/release/vc_redist.x64.exe (the 2015-2022 package covers "2019"). Without it the MSI stops with *"This application requires Visual Studio 2019 x64 Redistributable"*. Fresh laptops usually lack it.
2. Run it. When **MySQL Configurator** opens, use: config type *Development Computer*, port **3306**, Windows service name **MySQL84**, start at system startup on.
3. Set the **root password yourself** on the Accounts screen. Store it in a password manager. Never paste it into a chat with Claude.
4. Keep clicking Next until the **Execute** page, and press **Execute**. Wait for every line to go green, then Finish.
   *Lesson from the first PC: closing the Configurator before Execute leaves MySQL installed but not set up, with no service and no data folder.*

**Check (cmd or PowerShell):**

```powershell
& "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u root -p -e "SELECT VERSION();"
```

It asks for the root password and prints `8.4.x`.

### Route B: no admin rights (what the first PC uses)

Without admin rights you cannot install a Windows service, but MySQL runs fine as a normal background program. It will not start on its own after a reboot; you start it with a script.

1. Download the **ZIP Archive** (not the MSI) of MySQL 8.4 LTS for Windows x64 from https://dev.mysql.com/downloads/mysql/.
   The ZIP needs the same Visual C++ Redistributable as Route A; install it first.
2. Unzip it into `C:\mysql`, then rename the folder it creates (`mysql-8.4.x-winx64`) to `server`, so that `C:\mysql\server\bin\mysqld.exe` exists.
3. Create the data folder and initialize it (**cmd**):

```bat
mkdir C:\mysql\data
C:\mysql\server\bin\mysqld.exe --initialize --basedir=C:\mysql\server --datadir=C:\mysql\data --console
```

   The last lines print `A temporary password is generated for root@localhost: <temp>`. Copy that temporary password somewhere safe for the next step.

4. Create `C:\mysql\start-mysql.cmd` with this content:

```bat
@echo off
start "MySQL" /min C:\mysql\server\bin\mysqld.exe --basedir=C:\mysql\server --datadir=C:\mysql\data --port=3306 --bind-address=127.0.0.1 --console
```

   `--bind-address=127.0.0.1` keeps MySQL reachable only from this PC.

5. Double-click `start-mysql.cmd`. A minimized window titled "MySQL" appears. Leave it open; closing it stops MySQL.
6. Set your own root password (**cmd**). It asks for the temporary password from step 3:

```bat
C:\mysql\server\bin\mysql.exe -u root -p --connect-expired-password -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '<your-new-root-password>';"
```

**Check:**

```bat
C:\mysql\server\bin\mysql.exe -u root -p -e "SELECT VERSION();"
```

prints `8.4.x`. After any reboot, run `start-mysql.cmd` again before using the project.

The commands below use the Route A path `"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe"`. On Route B, replace it with `C:\mysql\server\bin\mysql.exe`. Type the path itself; do not type angle brackets.

---

## 6. Load the demo data and create the users

### 6a. Choose the read-only password

Open `examples\readonly_user.sql` in Notepad and replace `CHANGE_ME` with a password of your own. Save. The translator logs in as this user, so it should not be the same as root.

### 6b. Run the scripts

From the project folder in **cmd** (the `<` redirect does not work in PowerShell):

```bat
cd C:\code\nl-sql
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u root -p < examples\retail_demo.sql
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u root -p < examples\readonly_user.sql
```

`retail_demo.sql` is 2.8 MB and takes up to a minute. It creates the `nlsql_demo` database (10 tables, 15,000 orders, 2,000 customers).

Prefer a GUI? In MySQL Workbench: File > Open SQL Script, pick the file, press the lightning bolt. Same result.

### 6c. Create the test database

The tests build and drop their own small tables, so they need a separate throwaway database where the user can write. Open a root prompt:

```bat
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u root -p
```

and run:

```sql
CREATE DATABASE IF NOT EXISTS shop_test;
GRANT ALL PRIVILEGES ON shop_test.* TO 'nlsql_ro'@'localhost';
FLUSH PRIVILEGES;
exit
```

`nlsql_ro` still has only SELECT on `nlsql_demo`. Write rights are only on `shop_test`, which holds nothing you care about.

**Check (cmd):**

```bat
"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u nlsql_ro -p -e "SELECT COUNT(*) FROM nlsql_demo.orders; DELETE FROM nlsql_demo.orders WHERE 1=0;"
```

The count prints `15000`, and the DELETE fails with *"DELETE command denied"*. That failure is the result you want: it proves the user cannot change the demo data.

---

## 7. Run the tests against MySQL

In PowerShell with the venv active:

```powershell
$env:NLSQL_MYSQL_URL = "mysql+pymysql://nlsql_ro:<ro-password>@localhost/shop_test"
pytest tests
```

**Check:** all pass, with 2 expected skips (Postgres not configured, and the type-check test that MySQL can't catch because it silently converts types). The first PC got **24 passed, 2 skipped**.

If the password contains `@`, `:`, `/` or `#`, it must be URL-encoded in the URL (for example `@` becomes `%40`). Easier: pick a password with letters and digits only.

---

## 8. Set your Anthropic API key

Get a key at https://console.anthropic.com (Settings > API Keys). Set it yourself; do not paste it into a chat.

For the current window only:

```powershell
$env:ANTHROPIC_API_KEY = "<your-key>"
```

To keep it for future windows (takes effect in **new** windows):

```powershell
setx ANTHROPIC_API_KEY "<your-key>"
```

Optional: `NLSQL_MODEL` picks a different Claude model, `NLSQL_EFFORT` sets effort (`low`, `medium`, `high`).

---

## 9. Ask questions

### Quick try on SQLite (no MySQL needed)

```powershell
python examples\make_shop_db.py sqlite:///shop.db
nlsql ask "Revenue by country from shipped orders" --db sqlite:///shop.db --run
nlsql explain "SELECT c.name, COUNT(*) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY 1" --db sqlite:///shop.db
```

### On the MySQL demo

```powershell
$db = "mysql+pymysql://nlsql_ro:<ro-password>@localhost/nlsql_demo"
nlsql schema --db $db
nlsql ask "Which product category earned the most in 2025?" --db $db --run
nlsql ask "Top 10 customers by total spend, with their region" --db $db --run --check-meaning
nlsql explain "SELECT region_id, COUNT(*) FROM stores GROUP BY region_id" --db $db
nlsql transpile "SELECT TOP 5 * FROM orders" --from tsql --to mysql
```

**Check:** `ask` prints a SQL query and then a table of rows. A reply starting with *"Need clarification"* is the system working as designed for a vague question; make the question more specific.

---

## 9b. No API key? Use a free local model (Ollama)

Questions and explanations need a language model. If you can't pay for the Anthropic API, run one on your own PC for free.

1. Install Ollama for Windows from https://ollama.com/download and open it once (a llama icon appears near the clock).
2. Download a coding model (about 4.7 GB, one time). In PowerShell:

```powershell
ollama pull qwen2.5-coder:7b
```

3. Tell nlsql to use it (current window only; use `setx NLSQL_LLM ollama` to keep it):

```powershell
$env:NLSQL_LLM = "ollama"
nlsql ask "Which product category earned the most in 2025?" --db "mysql+pymysql://nlsql_ro:<ro-password>@localhost/nlsql_demo" --run
```

The first question takes longer while the model loads. A PC with an NVIDIA graphics card runs it much faster than one without. Expect more wrong answers than with Claude, especially for questions that join several tables: the checks reject invalid SQL, but not SQL that runs and answers a different question. Check results you rely on.

---

## 9c. The website (localhost only, free)

A page in your browser for both directions: type a question and get checked SQL plus the rows, or paste SQL and get a plain-language explanation. It runs only on your computer and uses Ollama (step 9b), so no API key.

**One time**, in `C:\code\nl-sql` with the venv active (after copying the new code over the old folder):

```powershell
pip install -e ".[mysql,web,test]"
pytest -q
```

**Every time** you want to use it (Ollama and MySQL must be running):

```powershell
cd C:\code\nl-sql
.\.venv\Scripts\Activate.ps1
$env:NLSQL_DB = "mysql+pymysql://nlsql_ro:<ro-password>@localhost/nlsql_demo"
nlsql-web
```

Your browser opens `http://127.0.0.1:8000`. Keep the PowerShell window open while you use the site; press `Ctrl+C` there to stop it. `nlsql-web --port 8001` if 8000 is taken.

Business rules (what "revenue" means, that cancelled orders don't count, and so on) are in `examples\business_rules.txt`. Edit them in the site's **Business rules** tab or in the file. Set `$env:NLSQL_RULES` to use a different file.

---

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `nlsql` is not recognized | venv not active | `.\.venv\Scripts\Activate.ps1` |
| `Can't connect to MySQL server on 'localhost'` | MySQL not running | Route A: start the `MySQL84` service. Route B: run `start-mysql.cmd` |
| `Access denied for user 'nlsql_ro'` | Password in the URL differs from `readonly_user.sql` | Fix the URL, or re-run 6b with the right password |
| `Unknown database 'shop_test'` | Step 6c skipped | Run 6c |
| `Cannot reach Ollama` | Ollama app not running | Open Ollama from the Start menu |
| `AuthenticationError` / 401 from Anthropic | Key missing or wrong in this window | Step 8; `setx` only affects new windows |
| `ModuleNotFoundError: pymysql` | Installed without the `mysql` extra | `pip install -e ".[mysql,test]"` |
| `Set NLSQL_DB to your database URL first` | The website doesn't know which database to use | Set `$env:NLSQL_DB` in the same window (9c) |
| `nlsql-web` is not recognized / `No module named fastapi` | Installed without the `web` extra | `pip install -e ".[mysql,web,test]"` |
| Site shows "Ollama not running" or "model not pulled" | Ollama closed, or step 9b skipped | Open Ollama; `ollama pull qwen2.5-coder:7b` |
| `Invalid host header` | Opened the site by a name other than localhost/127.0.0.1 | Use `http://127.0.0.1:8000` |
| Port 3306 already in use | Another MySQL is running | Stop it, or use `--port=3307` in the start script and `localhost:3307` in URLs |

---

## Want Claude to do this for you?

Install the **Claude desktop app** on this PC, sign in, and connect the PC to the project with **Remote Control**. Claude can then run steps 1 to 4, 7 and 9 directly. You still set the MySQL root password and the API key yourself; Claude never handles those.
