-- Run once as an admin. The translator should connect as this user, never as root.
CREATE USER IF NOT EXISTS 'nlsql_ro'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT SELECT, SHOW VIEW ON nlsql_demo.* TO 'nlsql_ro'@'localhost';
FLUSH PRIVILEGES;
