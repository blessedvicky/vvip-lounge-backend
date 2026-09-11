"""
Local logic tests using a tiny in-memory fake of the Supabase client, so the
core flows (register, conflict detection, login, remove-frees-house,
complaints, renewals) can be verified without any network access.
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

    def select(self, *_args, **_kwargs):
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

        # select
        matched = self._matching_rows(table)
        enriched = [self._enrich(dict(r)) for r in matched]
        if self.single:
            return FakeResult(enriched[0] if enriched else None)
        return FakeResult(enriched)

    def _enrich(self, row):
        # Mimic Supabase's nested-select for houses(*, tenants(*))
        if self.table_name == "houses":
            tenants = [t for t in self.store["tenants"] if t["house_id"] == row["id"]]
            row["tenants"] = tenants
        return row


class FakeSupabase:
    def __init__(self):
        self.store = {"houses": [], "tenants": [], "complaints": [], "renewals": []}

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

    # Seed two houses
    fake.store["houses"] = [
        {"id": 1, "block": "old", "house_num": 1, "rent": 25000, "profile_complete": False, "condition_notes": []},
        {"id": 2, "block": "new", "house_num": 1, "rent": 30000, "profile_complete": False, "condition_notes": []},
    ]

    # 1. Register succeeds for an open house
    r = client.post("/api/register", json={
        "name": "Faith Chebet", "block": "old", "house_num": 1,
        "course": "BCom", "phone": "0700000000",
        "emergency_name": "Sam", "emergency_phone": "0711111111",
        "roommates": [], "photo_url": "http://x/photo.jpg"
    })
    assert r.status_code == 200, r.get_json()
    tenant_id = r.get_json()["tenant"]["id"]
    print("PASS: register succeeds on open house")

    # 2. Second registration on the same house is rejected as taken
    r = client.post("/api/register", json={"name": "Someone Else", "block": "old", "house_num": 1})
    assert r.status_code == 409, r.get_json()
    print("PASS: duplicate registration on same house rejected (409)")

    # 3. Login with correct name matches
    r = client.post("/api/login", json={"name": "faith chebet", "block": "old", "house_num": 1})
    assert r.status_code == 200, r.get_json()
    print("PASS: login with matching name (case-insensitive) succeeds")

    # 4. Login with wrong name is rejected
    r = client.post("/api/login", json={"name": "Wrong Name", "block": "old", "house_num": 1})
    assert r.status_code == 401
    print("PASS: login with wrong name rejected (401)")

    # 5. Landlord removal requires admin secret
    r = client.post(f"/api/tenant/{tenant_id}/remove")
    assert r.status_code == 403
    print("PASS: remove-tenant without admin secret rejected (403)")

    # 6. Landlord removal with correct secret frees the house
    r = client.post(f"/api/tenant/{tenant_id}/remove?secret=test-secret")
    assert r.status_code == 200, r.get_json()
    house = next(h for h in fake.store["houses"] if h["id"] == 1)
    assert house["profile_complete"] is False
    assert all(t["id"] != tenant_id for t in fake.store["tenants"])
    print("PASS: remove-tenant clears profile_complete and deletes tenant row")

    # 7. House is registrable again after removal
    r = client.post("/api/register", json={"name": "New Tenant", "block": "old", "house_num": 1})
    assert r.status_code == 200, r.get_json()
    print("PASS: house is open again after removal")

    # 8. Complaints: create then resolve
    new_tenant_id = r.get_json()["tenant"]["id"]
    r = client.post("/api/complaints", json={"tenant_id": new_tenant_id, "type": "Plumbing", "message": "Leaking tap"})
    assert r.status_code == 200
    complaint_id = fake.store["complaints"][0]["id"]
    r = client.get("/api/complaints?secret=test-secret")
    assert r.status_code == 200 and len(r.get_json()) == 1
    r = client.post(f"/api/complaints/{complaint_id}/resolve?secret=test-secret")
    assert r.status_code == 200
    assert fake.store["complaints"][0]["status"] == "resolved"
    print("PASS: complaint create + admin resolve flow")

    # 9. Renewal interest recorded
    r = client.post("/api/renew", json={"tenant_id": new_tenant_id, "block": "old", "house_num": 1, "tenant_name": "New Tenant"})
    assert r.status_code == 200
    r = client.get("/api/renewals?secret=test-secret")
    assert r.status_code == 200 and len(r.get_json()) == 1
    print("PASS: renewal interest recorded and listable by admin")

    print("\nAll tests passed.")


if __name__ == "__main__":
    run_tests()
