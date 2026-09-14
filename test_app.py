"""
Local logic tests using a tiny in-memory fake of the Supabase client.
Run: python3 test_app.py
"""
import copy


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table_name, op=None, payload=None):
        self.store = store
        self.table_name = table_name
        self.op = op
        self.payload = payload
        self.filters = []
        self.single = False
        self.select_fields = "*"

    def select(self, *args, **_kwargs):
        self.select_fields = args[0] if args else "*"
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _matching_rows(self, table):
        rows = table
        for field, value in self.filters:
            rows = [r for r in rows if r.get(field) == value]
        return rows

    def execute(self):
        table = self.store[self.table_name]

        if self.op == "insert":
            new_id = (max([r["id"] for r in table], default=0)) + 1
            row = copy.deepcopy(self.payload)
            row["id"] = new_id
            table.append(row)
            return FakeResult([row])

        if self.op == "update":
            matched = self._matching_rows(table)
            for row in matched:
                row.update(self.payload)
            return FakeResult(matched)

        if self.op == "delete":
            matched = self._matching_rows(table)
            for row in matched:
                table.remove(row)
            return FakeResult(matched)

        matched = self._matching_rows(table)
        enriched = [self._enrich(dict(r)) for r in matched]
        if self.single:
            return FakeResult(enriched[0] if enriched else None)
        return FakeResult(enriched)

    def _enrich(self, row):
        if self.table_name == "houses" and "tenants" in self.select_fields:
            tenants = [t for t in self.store["tenants"] if t["house_id"] == row["id"]]
            row["tenants"] = tenants
        return row


class FakeSupabase:
    def __init__(self):
        self.store = {"houses": [], "tenants": [], "complaints": [], "renewals": [], "settings": []}

    def table(self, name):
        return _TableHandle(self.store, name)


class _TableHandle:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def select(self, *args, **kwargs):
        return FakeQuery(self.store, self.name).select(*args, **kwargs)

    def insert(self, payload):
        return FakeQuery(self.store, self.name, op="insert", payload=payload)

    def update(self, payload):
        return FakeQuery(self.store, self.name, op="update", payload=payload)

    def delete(self):
        return FakeQuery(self.store, self.name, op="delete")


def run_tests():
    import app as appmod

    fake = FakeSupabase()
    appmod.sb = fake
    appmod.ADMIN_SECRET = "test-secret"
    client = appmod.app.test_client()
    ADMIN_HEADERS = {"X-Admin-Secret": "test-secret"}

    fake.store["houses"] = [
        {"id": 1, "block": "A", "house_num": 1, "rent": 25000, "profile_complete": False, "condition_notes": []},
        {"id": 2, "block": "B", "house_num": 1, "rent": 30000, "profile_complete": False, "condition_notes": []},
        {"id": 3, "block": "C", "house_num": 1, "rent": 30000, "profile_complete": False, "condition_notes": []},
    ]
    fake.store["settings"] = [{
        "id": 1, "estate_name": "VVIP Lounge", "color_primary": "#1E6B47", "color_accent": "#D19A3D",
        "rent_a": 25000, "rent_b": 30000, "rent_c": 30000
    }]

    # 1. Public houses endpoint returns no tenant details field at all
    r = client.get("/api/houses")
    assert r.status_code == 200
    assert "tenants" not in r.get_json()[0]
    print("PASS: public /api/houses excludes tenant details")

    # 2. Full houses endpoint requires admin header
    r = client.get("/api/houses/full")
    assert r.status_code == 403
    r = client.get("/api/houses/full", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert "tenants" in r.get_json()[0]
    print("PASS: /api/houses/full gated by admin header, includes tenants")

    # 3. Register succeeds for an open house in block C
    r = client.post("/api/register", json={
        "name": "Faith Chebet", "block": "C", "house_num": 1,
        "course": "BCom", "phone": "0700000000",
        "emergency_name": "Sam", "emergency_phone": "0711111111",
        "roommates": [], "photo_url": "data:image/jpeg;base64,xxx"
    })
    assert r.status_code == 200, r.get_json()
    tenant_id = r.get_json()["tenant"]["id"]
    print("PASS: register succeeds on open house in block C")

    # 4. Invalid house number for block (C only has 3 houses)
    r = client.post("/api/register", json={"name": "X", "block": "C", "house_num": 9})
    assert r.status_code == 400
    print("PASS: out-of-range house number for block rejected (400)")

    # 5. Duplicate registration on same house rejected
    r = client.post("/api/register", json={"name": "Someone Else", "block": "C", "house_num": 1})
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
    print("PASS: profile edit with correct confirm_name updates the record")

    # 9. Remove tenant requires admin header, then frees the house
    r = client.post(f"/api/tenant/{tenant_id}/remove")
    assert r.status_code == 403
    r = client.post(f"/api/tenant/{tenant_id}/remove", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    house = next(h for h in fake.store["houses"] if h["id"] == 3)
    assert house["profile_complete"] is False
    print("PASS: remove-tenant (admin header) frees the house")

    # 10. House is registrable again after removal
    r = client.post("/api/register", json={"name": "New Tenant", "block": "C", "house_num": 1})
    assert r.status_code == 200
    print("PASS: house open again after removal")

    # 11. Complaints + renewals flow (unchanged, admin header now)
    new_tenant_id = r.get_json()["tenant"]["id"]
    client.post("/api/complaints", json={"tenant_id": new_tenant_id, "type": "Plumbing", "message": "Leak"})
    r = client.get("/api/complaints", headers=ADMIN_HEADERS)
    assert r.status_code == 200 and len(r.get_json()) == 1
    complaint_id = fake.store["complaints"][0]["id"]
    r = client.post(f"/api/complaints/{complaint_id}/resolve", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    print("PASS: complaints create + admin resolve (header auth)")

    client.post("/api/renew", json={"tenant_id": new_tenant_id, "block": "C", "house_num": 1, "tenant_name": "New Tenant"})
    r = client.get("/api/renewals", headers=ADMIN_HEADERS)
    assert r.status_code == 200 and len(r.get_json()) == 1
    print("PASS: renewal interest recorded and listable by admin")

    # 12. Settings: public GET works, PUT requires admin header
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
    house_a = next(h for h in fake.store["houses"] if h["block"] == "A")
    assert house_a["rent"] == 27000
    print("PASS: admin settings update applies and propagates rent to house rows")

    print("\nAll tests passed.")


if __name__ == "__main__":
    run_tests()
