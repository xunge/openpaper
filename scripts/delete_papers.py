"""Delete papers and every row that references them (FK-driven)."""
import subprocess

PSQL = [
    "docker", "compose", "-f", "docker-compose.dev.yml", "exec", "-T", "postgres",
    "psql", "-U", "postgres", "-d", "annotated-paper", "-tAc",
]

def q(sql: str) -> str:
    return subprocess.run(PSQL + [sql], capture_output=True, text=True,
                          cwd="/home/jiang/projects/openpaper").stdout.strip()

paper_ids = q("select id from papers").splitlines()
print("papers to delete:", paper_ids)
if not paper_ids:
    raise SystemExit("nothing to delete")

quoted = ",".join(f"'{p}'" for p in paper_ids)

# Every table/column that points at papers, except papers itself.
refs = q("""
select conrelid::regclass || '|' || attname
from pg_constraint c
join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
where c.confrelid = 'papers'::regclass and c.contype = 'f'
""").splitlines()
print("referencing columns:", refs)

for ref in refs:
    if not ref:
        continue
    table, column = ref.split("|")
    if table == "papers":
        continue
    out = q(f"delete from {table} where {column} in ({quoted})")
    print(f"  {table}.{column}: {out}")

print("delete papers:", q(f"delete from papers where id in ({quoted})"))
print("remaining papers:", q("select count(*) from papers"))
