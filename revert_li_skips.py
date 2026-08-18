import sqlite3, os
os.chdir("/home/ubuntu/job_hunt_linkedin")
c = sqlite3.connect("apply_queue.db")
n = c.execute("UPDATE jobs SET status='pending', claimed_by=NULL WHERE portal='linkedin' AND status='skip' AND claimed_by IN ('li-w1','li-w2')").rowcount
c.commit()
print("reverted", n, "linkedin skips back to pending")
for r in c.execute("SELECT portal, status, COUNT(*) FROM jobs GROUP BY portal, status ORDER BY portal"):
    print(" ", r)
c.close()
