"""Run: python access_demo.py
Transparency levels and the task certification chain. Sample data."""
from hiwr_contacts import *
from hiwr_access import *

D = "2026-11-20"
db = connect()
add_plan_owner(db, "owner_alex", "setup")          # the account holder
maria = add_contact(db, "Maria Lopez", "host_ana", phone="910-555-0101")["id"]
o = create_order(db, "restaurant_live", "dine_in", [{"item": "Burger"}], "server_kim", contact_id=maria)

def blocked(f, *a, **k):
    try:
        f(*a, **k); return "allowed"
    except PermissionError as e:
        return f"BLOCKED: {e}"

print("1. LEVELS (account holder only)")
for m, l in [("cook_luis", 0), ("lead_rosa", 1), ("chef_marco", 2), ("accounting_sue", 3)]:
    set_level(db, m, l, "owner_alex")
    print(f"   {m:<15} -> {l} {LEVEL_NAMES[l]}")
print("   chef_marco sets cook_luis to Manager:", blocked(set_level, db, "cook_luis", 2, "chef_marco"))

print("\n2. WHAT EACH LEVEL SEES")
clients = lambda: [dict(r) for r in db.execute("SELECT display_name, e164 FROM contacts c JOIN contact_phones p ON p.contact_id=c.id")]
masked = lambda: [{"display_name": r["display_name"].split()[0], "phone": r["phone_masked"]} for r in db.execute("SELECT * FROM v_staff_contacts")]
costs = lambda: {"order": o, "food_cost": "$3.10", "price": "$14.00", "margin": "78%"}
payroll = lambda: {"week": "Nov 30", "total": "$4,068.00"}
for m in ("cook_luis", "lead_rosa", "chef_marco", "accounting_sue"):
    print(f"   {m}")
    print("     clients :", read(db, m, "client_records", clients, mask=masked))
    print("     costs   :", blocked(read, db, m, "order_costs", costs) if not can_see(db, m, "order_costs") else read(db, m, "order_costs", costs))
    print("     payroll :", blocked(read, db, m, "payroll", payroll) if not can_see(db, m, "payroll") else read(db, m, "payroll", payroll))

print("\n3. TASK CERTIFICATION CHAIN")
g1 = grant_task(db, "chef_marco", "inventory_audit", "owner_alex", D, can_delegate=True, expires_on="2027-06-30")
grant_task(db, "chef_marco", "roll_call", "owner_alex", D, can_delegate=True)
print("   owner_alex -> chef_marco: inventory_audit + roll_call, may certify others (audit expires 2027-06-30)")
g3 = grant_task(db, "lead_rosa", "inventory_audit", "chef_marco", D, expires_on="2028-01-01")
r = db.execute("SELECT expires_on FROM task_grants WHERE id=?", (g3,)).fetchone()
print(f"   chef_marco -> lead_rosa: inventory_audit (asked for 2028-01-01, capped to {r['expires_on']})")
grant_task(db, "cook_luis", "roll_call", "chef_marco", D)
print("   chef_marco -> cook_luis: roll_call")
print("   chef_marco -> lead_rosa may certify others:", blocked(grant_task, db, "lead_rosa", "roll_call", "chef_marco", D, can_delegate=True))
print("   lead_rosa  -> cook_luis inventory_audit  :", blocked(grant_task, db, "cook_luis", "inventory_audit", "lead_rosa", D))
print("   chef_marco -> cook_luis payroll_approval :", blocked(grant_task, db, "cook_luis", "payroll_approval", "chef_marco", D))
print("   chef_marco -> lead_rosa receiving_check  :", blocked(grant_task, db, "lead_rosa", "receiving_check", "chef_marco", D))
print("   Can lead_rosa run the inventory audit?", can_do(db, "lead_rosa", "inventory_audit", D))
print("   Can cook_luis take roll call?        ", can_do(db, "cook_luis", "roll_call", D))
print("   Note: a task never raises a level. Rosa audits counts (level 1) but still can't see order costs.")

print("\n4. ONE SYSTEM FOR MONEY APPROVALS")
print("   owner_alex -> chef_marco payroll_approval:", blocked(grant_task, db, "chef_marco", "payroll_approval", "owner_alex", D))
set_level(db, "manager_jo", 3, "owner_alex")
grant_task(db, "manager_jo", "payroll_approval", "owner_alex", D)
grant_task(db, "accounting_sue", "payroll_approval", "owner_alex", D)
a = request_money_action(db, "payroll", 9600, "Roll-call overtime, Nov 20", "lead_rosa")
approve(db, a, "manager_jo")
print("   Payroll action approved via task grants:", approve(db, a, "accounting_sue"))
print("   cook_luis tries to approve:", blocked(approve, db, a, "cook_luis"))

print("\n5. REVOKE A CERTIFIER")
revoke(db, g1, "owner_alex", reason="moved to the other location")
print("   owner_alex revokes chef_marco's inventory_audit")
print("   Can lead_rosa still audit? ", can_do(db, "lead_rosa", "inventory_audit", D), "(her grant came through Marco)")
print("   Can cook_luis still take roll call?", can_do(db, "cook_luis", "roll_call", D), "(Marco still holds roll_call)")

print("\n6. PERMISSIONS REPORT (account holder view)")
for row in permissions_report(db, D):
    print(f"   {row['member']:<15} level {row['level']:<12} tasks: {', '.join(row['tasks']) or '-'}")
print("   Access log entries:", db.execute("SELECT COUNT(*) FROM access_log").fetchone()[0],
      "| denied:", db.execute("SELECT COUNT(*) FROM access_log WHERE action LIKE 'denied%'").fetchone()[0])
