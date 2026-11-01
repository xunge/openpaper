"""Delete a test account and everything that references it."""
import subprocess
import sys

EMAIL = sys.argv[1]
PSQL = [
    "docker", "compose", "-f", "docker-compose.dev.yml", "exec", "-T", "postgres",
    "psql", "-U", "postgres", "-d", "annotated-paper", "-tAc",
]

def run(sql: str) -> str:
    return subprocess.run(PSQL + [sql], capture_output=True, text=True, cwd="/home/jiang/projects/openpaper").stdout.strip()

user_id = run(f"select id from users where email='{EMAIL}'")
if not user_id:
    print("no such user")
    raise SystemExit(0)

tables = [r for r in run(
    "select conrelid::regclass from pg_constraint where confrelid='users'::regclass"
).splitlines() if r]

for table in tables:
    if table == "papers":
        run(f"delete from paper_passages where paper_id in (select id from papers where user_id='{user_id}')")
    run(f"delete from {table} where user_id='{user_id}'")

run(f"delete from messages where conversation_id in (select id from conversations where user_id='{user_id}')")
run(f"delete from conversations where user_id='{user_id}'")
run(f"delete from users where id='{user_id}'")
print("remaining users:", run("select count(*) from users"))
