"""RED: R3+R4 regression - legacy queue must support worker claim queries."""
import sqlite3, tempfile, os
from workflow.schema import migrate_queue, QUEUE_VERSION

def _legacy_db():
    tmp = tempfile.mktemp(suffix='.db')
    c = sqlite3.connect(tmp)
    c.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT, source TEXT, status TEXT DEFAULT 'pending', claimed_by TEXT, result TEXT)")
    c.execute("INSERT INTO jobs VALUES ('j1','wellfound','https://x','Eng','wellfound','pending',NULL,NULL)")
    c.commit(); c.close()
    return tmp

def test_r3_claim_projection_survives_legacy_migration():
    db = _legacy_db()
    try:
        migrate_queue(db)
        c = sqlite3.connect(db)
        for portal in ('wellfound','internshala','yc','linkedin'):
            rows = c.execute(
                "SELECT id, url, title, location, company FROM jobs WHERE portal=? AND status='pending' ORDER BY prio DESC, rowid LIMIT 1",
                (portal,),
            ).fetchall()
            assert isinstance(rows, list)
        c.close()
    finally:
        os.unlink(db)

def test_r3_queue_version_includes_location_company_prio():
    assert QUEUE_VERSION >= 5

def test_r4_reset_sql_valid():
    import pathlib
    sql = pathlib.Path('reset_claims.sh').read_text()
    assert 'IS NOT IN' not in sql
    assert 'NOT IN' in sql
    # NULL-safe + portal scoping
    assert 'IS NULL' in sql
    assert 'portal' in sql.lower()
