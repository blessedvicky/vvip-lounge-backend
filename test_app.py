"""
Local logic tests using an in-memory fake of the direct-REST helpers
(pg_select/pg_insert/pg_update/pg_delete), so the full request/response
logic in app.py can be verified without any network access.
Run: python3 test_app.py
"""
import re


def build_fake_store():
    return {"houses": [], "tenants": [], "complaints": [], "renewals": [], "settings": []}


def make_fakes(store):
    def next_id(table):
        return max([r["id"] for r in store[table]], default=0) + 1

    def match_row(row, params):
        for key, val in (params or {}).items():
            if key in ("select", "order"):
                continue
            if isinstance(val, str) and val.startswith("eq."):
                target = val[3:]
                if str(row.get(key)) != target:
                    return False
        return True

    def embed(table, row, select_str):
        row = dict(row)
        if table == "houses" and select_str and "tenants" in select_str:
            row["tenants"] = [t for t in store["tenants"] if t["house_id"] == row["id"]]
        if table == "complaints" and select_str and "tenants" in select_str:
            t = next((t for t in store["tenants"] if t["id"] == row.get("tenant_id")), None)
            if t:
                h = next((h for h in store["houses"] if h["id"] == t["house_id"]), None)
                row["tenants"] = {
                    "name": t["name"],
                    "houses": {"block": h["block"], "house_num": h["house_num"]} if h else None,
                }
            else:
                row["tenants"] = None
        return row

    def fake_select(table, params=None):
        params = params or {}
        rows = [r for r in store[table] if match_row(r, params)]
        select_str = params.get("select", "")
        return [embed(table, r, select_str) for r in rows]

    def fake_insert(table, payload):
        row = dict(payload)
        if table == "tenants":
            row.setdefault("approved", False)
            row.setdefault("paid", False)
        row["id"] = next_id(table)
        store[table].append(row)
        return [dict(row)]

    def fake_update(table, payload, params):
        matched = [r for r in store[table] if match_row(r, params)]
        for r in matched:
            r.update(payload)
        return [dict(r) for r in matched]

    def fake_delete(table, params):
        matched = [r for r in store[table] if match_row(r, params)]
        store[table] = [r for r in store[table] if r not in matched]
        return [dict(r) for r in matched]

    return fake_select, fake_insert, fake_update, fake_delete


def run_tests():
    import app as appmod

    store = build_fake_store()
    fake_select, fake_insert, fake_update, fake_delete = make_fakes(store)
    appmod.pg_select = fake_select
    appmod.pg_insert = fake_insert
    appmod.pg_update = fake_update
    appmod.pg_delete = fake_delete
    appmod.ADMIN_SECRET = "test-secret"
    client = appmod.app.test_client()
    ADMIN_HEADERS = {"X-Admin-Secret": "test-secret"}

    store["houses"] = [
        {"id": 1, "block": "A", "house_num": 1, "rent": 25000, "profile_complete": False, "condition_notes": []},
        {"id": 2, "block": "B", "house_num": 1, "rent": 30000, "profile_complete": False, "condition_notes": []},
        {"id": 3, "block": "C", "house_num": 1, "rent": 30000, "profile_complete": False, "condition_notes": []},
    ]
    store["settings"] = [{
        "id": 1, "estate_name": "VVIP Lounge", "color_primary": "#1E6B47", "color_accent": "#D19A3D",
        "rent_a": 25000, "rent_b": 30000, "rent_c": 30000
    }]

    # 1. Public houses endpoint returns no tenant details field at all
    r = client.get("/api/houses")
    assert r.status_code == 200, r.get_json()
    assert "tenants" not in r.get_json()[0]
    print("PASS: public /api/houses excludes tenant details")

    # 2. Full houses endpoint requires admin header
    r = client.get("/api/houses/full")
    assert r.status_code == 403
    r = client.get("/api/houses/full", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert "tenants" in r.get_json()[0]
    print("PASS: /api/houses/full gated by admin header, includes tenants")

    # 3a. Registration rejected without terms acceptance
    r = client.post("/api/register", json={
        "name": "No Terms", "block": "C", "house_num": 2,
        "roommates": [], "photo_url": "x"
    })
    assert r.status_code == 400 and r.get_json()["error"] == "terms_not_accepted"
    print("PASS: registration rejected when terms not accepted")

    # 3. Register succeeds for an open house in block C
    r = client.post("/api/register", json={
        "name": "Faith Chebet", "block": "C", "house_num": 1,
        "course": "BCom", "phone": "0700000000",
        "emergency_name": "Sam", "emergency_phone": "0711111111",
        "roommates": [], "photo_url": "data:image/jpeg;base64,xxx", "agreed_terms": True
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["tenant"]["name"] == "Faith Chebet"
    tenant_id = r.get_json()["tenant"]["id"]
    print("PASS: register succeeds on open house in block C, returns tenant data")

    # 3b. New registrations start unapproved
    assert r.get_json()["tenant"]["approved"] is False
    print("PASS: new registration starts as approved=false (pending landlady confirmation)")

    # 3c. Approve requires admin header, then flips the flag
    r2 = client.post(f"/api/tenant/{tenant_id}/approve")
    assert r2.status_code == 403
    r2 = client.post(f"/api/tenant/{tenant_id}/approve", headers=ADMIN_HEADERS)
    assert r2.status_code == 200 and r2.get_json()["tenant"]["approved"] is True
    print("PASS: admin approve endpoint flips approved to true")

    # 4. Invalid house number for block (C only has 3 houses)
    r = client.post("/api/register", json={"name": "X", "block": "C", "house_num": 9, "agreed_terms": True})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_house_number"
    print("PASS: out-of-range house number for block rejected (400)")

    # 5. Duplicate registration on same house rejected
    r = client.post("/api/register", json={"name": "Someone Else", "block": "C", "house_num": 1, "agreed_terms": True})
    assert r.status_code == 409
    print("PASS: duplicate registration on same house rejected (409)")

    # 6. Login with correct name matches
    r = client.post("/api/login", json={"name": "faith chebet", "block": "C", "house_num": 1})
    assert r.status_code == 200
    print("PASS: login with matching name succeeds")

    # 7. Login with wrong name rejected
    r = client.post("/api/login", json={"name": "Wrong Name", "block": "C", "house_num": 1})
    assert r.status_code == 401
    print("PASS: login with wrong name rejected (401)")

    # 8. Edit own profile requires matching name
    r = client.put(f"/api/tenant/{tenant_id}", json={"confirm_name": "wrong name", "phone": "0799999999"})
    assert r.status_code == 401
    print("PASS: profile edit with wrong confirm_name rejected (401)")

    r = client.put(f"/api/tenant/{tenant_id}", json={"confirm_name": "Faith Chebet", "phone": "0799999999"})
    assert r.status_code == 200
    assert r.get_json()["tenant"]["phone"] == "0799999999"
    print("PASS: profile edit with correct confirm_name updates and returns the record")

    # 9. Remove tenant requires admin header, then frees the house
    r = client.post(f"/api/tenant/{tenant_id}/remove")
    assert r.status_code == 403
    r = client.post(f"/api/tenant/{tenant_id}/remove", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    house = next(h for h in store["houses"] if h["id"] == 3)
    assert house["profile_complete"] is False
    print("PASS: remove-tenant (admin header) frees the house")

    # 10. House is registrable again after removal
    r = client.post("/api/register", json={"name": "New Tenant", "block": "C", "house_num": 1, "agreed_terms": True})
    assert r.status_code == 200
    print("PASS: house open again after removal")

    # 11. Complaints + renewals flow
    new_tenant_id = r.get_json()["tenant"]["id"]
    client.post("/api/complaints", json={"tenant_id": new_tenant_id, "type": "Plumbing", "message": "Leak"})
    r = client.get("/api/complaints", headers=ADMIN_HEADERS)
    assert r.status_code == 200 and len(r.get_json()) == 1
    complaint_id = store["complaints"][0]["id"]
    r = client.post(f"/api/complaints/{complaint_id}/resolve", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    print("PASS: complaints create + admin resolve (header auth)")

    r = client.post("/api/renew", json={"tenant_id": new_tenant_id, "block": "C", "house_num": 1, "tenant_name": "New Tenant"})
    assert r.status_code == 400 and r.get_json()["error"] == "payment_not_confirmed"
    print("PASS: renewal rejected without payment confirmation")

    client.post("/api/renew", json={"tenant_id": new_tenant_id, "block": "C", "house_num": 1, "tenant_name": "New Tenant", "payment_confirmed": True})
    r = client.get("/api/renewals", headers=ADMIN_HEADERS)
    assert r.status_code == 200 and len(r.get_json()) == 1
    print("PASS: renewal interest recorded and listable by admin")

    # 12. Settings: public GET works, PUT requires admin header, rent propagates
    r = client.get("/api/settings")
    assert r.status_code == 200 and r.get_json()["estate_name"] == "VVIP Lounge"
    print("PASS: public GET /api/settings returns current settings")

    r = client.put("/api/settings", json={"estate_name": "Sunrise Court"})
    assert r.status_code == 403
    print("PASS: PUT /api/settings without admin header rejected (403)")

    r = client.put("/api/settings", json={"estate_name": "Sunrise Court", "rent_a": 27000}, headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.get_json()["estate_name"] == "Sunrise Court"
    assert r.get_json()["rent_a"] == 27000
    house_a = next(h for h in store["houses"] if h["block"] == "A")
    assert house_a["rent"] == 27000
    print("PASS: admin settings update applies and propagates rent to house rows")

    # 13. CORS preflight carries the right headers (Flask handles OPTIONS automatically, status 200)
    r = client.options("/api/houses")
    assert r.status_code == 200
    assert r.headers.get("Access-Control-Allow-Origin") == "*"
    print("PASS: CORS preflight headers present")

    # 14. REST_URL normalization tolerates a SUPABASE_URL that already ends in /rest/v1
    assert appmod._normalize_base_url("https://xxxx.supabase.co") == "https://xxxx.supabase.co"
    assert appmod._normalize_base_url("https://xxxx.supabase.co/rest/v1") == "https://xxxx.supabase.co"
    assert appmod._normalize_base_url("https://xxxx.supabase.co/rest/v1/") == "https://xxxx.supabase.co"
    print("PASS: SUPABASE_URL normalization handles both plain and /rest/v1-suffixed values")

    print("\nAll tests passed.")


if __name__ == "__main__":
    run_tests()
