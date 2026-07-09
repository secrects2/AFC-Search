"""Recalculate price_diff and statuses after migrating candidates."""
import sqlite3

conn = sqlite3.connect("data/price_monitor.db")
cur = conn.cursor()

# Get all candidates with their latest price and correct suggested_price
cur.execute("""
    SELECT pc.id, pc.status, p.suggested_price, ps.price, ps.id as snap_id
    FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
    LEFT JOIN price_snapshots ps ON ps.candidate_id = pc.id
    WHERE ps.price IS NOT NULL AND p.suggested_price IS NOT NULL
""")
rows = cur.fetchall()

updated_snaps = 0
updated_status = 0

for cand_id, old_status, suggested, actual_price, snap_id in rows:
    diff = actual_price - suggested
    
    # Update snapshot price_diff
    cur.execute("UPDATE price_snapshots SET price_diff = ? WHERE id = ?", (diff, snap_id))
    updated_snaps += 1

# Now recalculate candidate statuses based on latest snapshot
cur.execute("""
    SELECT pc.id, pc.status, p.suggested_price
    FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
    WHERE p.suggested_price IS NOT NULL
""")
candidates = cur.fetchall()

for cand_id, old_status, suggested in candidates:
    # Get latest price
    cur.execute("""
        SELECT price FROM price_snapshots 
        WHERE candidate_id = ? AND price IS NOT NULL 
        ORDER BY checked_at DESC LIMIT 1
    """, (cand_id,))
    row = cur.fetchone()
    
    if row and row[0] is not None:
        price = row[0]
        diff = price - suggested
        new_status = "suspected_violation" if diff < 0 else "normal"
        if new_status != old_status:
            cur.execute("UPDATE product_candidates SET status = ? WHERE id = ?", (new_status, cand_id))
            updated_status += 1
            print(f"  #{cand_id}: {old_status} -> {new_status} (price={price}, suggested={suggested}, diff={diff})")

conn.commit()
print(f"\nUpdated {updated_snaps} snapshot price_diffs")
print(f"Fixed {updated_status} candidate statuses")

# Show current violations
print("\n=== Current violations ===")
cur.execute("""
    SELECT pc.id, p.product_name, p.suggested_price, ps.price, (ps.price - p.suggested_price) as diff, pc.url
    FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
    JOIN price_snapshots ps ON ps.candidate_id = pc.id
    WHERE pc.status = 'suspected_violation'
    AND ps.price IS NOT NULL
    ORDER BY diff ASC
""")
for r in cur.fetchall():
    print(f"  #{r[0]} {r[1]} suggested=${r[2]} actual=${r[3]} diff={r[4]}")

conn.close()
