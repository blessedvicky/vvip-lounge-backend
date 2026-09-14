import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------- CORS (kept dependency-free, no flask_cors needed) ----------
@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Secret"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return resp


SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")

VALID_BLOCKS = {"A": 6, "B": 6, "C": 3}

REST_URL = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else None
REST_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY or "",
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}" if SUPABASE_SERVICE_KEY else "",
    "Content-Type": "application/json",
}


# ---------- Thin direct-REST helpers (talk to PostgREST ourselves — no
# supabase-py in the middle, which is what was mis-parsing valid empty
# responses as errors). Every filter value must already be PostgREST-style,
# e.g. {"id": "eq.5"}. ----------
def pg_select(table, params=None):
    r = requests.get(f"{REST_URL}/{table}", headers=REST_HEADERS, params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json() if r.text else []


def pg_insert(table, payload):
    headers = {**REST_HEADERS, "Prefer": "return=representation"}
    r = requests.post(f"{REST_URL}/{table}", headers=headers, json=payload, timeout=15)
    r.raise_for_status()
    return r.json() if r.text else []


def pg_update(table, payload, params):
    headers = {**REST_HEADERS, "Prefer": "return=representation"}
    r = requests.patch(f"{REST_URL}/{table}", headers=headers, params=params, json=payload, timeout=15)
    r.raise_for_status()
    return r.json() if r.text else []


def pg_delete(table, params):
    headers = {**REST_HEADERS, "Prefer": "return=representation"}
    r = requests.delete(f"{REST_URL}/{table}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json() if r.text else []


def require_admin(req):
    secret = req.headers.get("X-Admin-Secret")
    return ADMIN_SECRET is not None and secret == ADMIN_SECRET


def house_full(block, house_num):
    rows = pg_select("houses", {
        "select": "*,tenants(*)",
        "block": f"eq.{block}",
        "house_num": f"eq.{house_num}",
    })
    return rows[0] if rows else None


# ---------- PUBLIC: occupancy only, no tenant details ----------
@app.route("/api/houses", methods=["GET"])
def list_houses_public():
    rows = pg_select("houses", {
        "select": "id,block,house_num,rent,condition_notes,profile_complete",
        "order": "block.asc,house_num.asc",
    })
    return jsonify(rows)


# ---------- ADMIN ONLY: full tenant details ----------
@app.route("/api/houses/full", methods=["GET"])
def list_houses_full():
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    rows = pg_select("houses", {"select": "*,tenants(*)", "order": "block.asc,house_num.asc"})
    return jsonify(rows)


# ---------- TENANT REGISTRATION (first-time sign-in) ----------
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(force=True)
    block = data.get("block")
    house_num = data.get("house_num")
    name = (data.get("name") or "").strip()

    if not name or block not in VALID_BLOCKS or not house_num:
        return jsonify({"error": "missing_fields"}), 400
    if not (1 <= int(house_num) <= VALID_BLOCKS[block]):
        return jsonify({"error": "invalid_house_number"}), 400

    house = house_full(block, house_num)
    if not house:
        return jsonify({"error": "house_not_found"}), 404
    if house["profile_complete"]:
        return jsonify({"error": "house_taken"}), 409

    tenant = {
        "house_id": house["id"],
        "name": name,
        "course": data.get("course", ""),
        "phone": data.get("phone", ""),
        "emergency_name": data.get("emergency_name", ""),
        "emergency_phone": data.get("emergency_phone", ""),
        "roommates": data.get("roommates", []),
        "photo_url": data.get("photo_url", ""),
    }
    inserted_rows = pg_insert("tenants", tenant)
    pg_update("houses", {"profile_complete": True}, {"id": f"eq.{house['id']}"})
    inserted = inserted_rows[0] if inserted_rows else None
    return jsonify({"tenant": inserted, "house": house})


# ---------- TENANT LOGIN (returning sign-in: name + block + house_num) ----------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    block = data.get("block")
    house_num = data.get("house_num")
    name = (data.get("name") or "").strip().lower()

    house = house_full(block, house_num)
    if not house or not house.get("tenants"):
        return jsonify({"error": "not_found"}), 404

    tenant = house["tenants"][0] if isinstance(house["tenants"], list) else house["tenants"]
    if not tenant or tenant["name"].strip().lower() != name:
        return jsonify({"error": "name_mismatch"}), 401

    return jsonify({"tenant": tenant, "house": house})


# ---------- TENANT: EDIT OWN PROFILE ----------
@app.route("/api/tenant/<int:tenant_id>", methods=["PUT"])
def update_tenant(tenant_id):
    data = request.get_json(force=True)
    confirm_name = (data.get("confirm_name") or "").strip().lower()

    existing_rows = pg_select("tenants", {"id": f"eq.{tenant_id}"})
    existing = existing_rows[0] if existing_rows else None
    if not existing:
        return jsonify({"error": "not_found"}), 404
    if existing["name"].strip().lower() != confirm_name:
        return jsonify({"error": "name_mismatch"}), 401

    updatable = {}
    for field in ("course", "phone", "emergency_name", "emergency_phone", "roommates", "photo_url"):
        if field in data:
            updatable[field] = data[field]

    if updatable:
        updated_rows = pg_update("tenants", updatable, {"id": f"eq.{tenant_id}"})
        refreshed = updated_rows[0] if updated_rows else existing
    else:
        refreshed = existing

    return jsonify({"tenant": refreshed})


# ---------- LANDLORD: REMOVE A TENANT (frees the house on both sides) ----------
@app.route("/api/tenant/<int:tenant_id>/remove", methods=["POST"])
def remove_tenant(tenant_id):
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403

    rows = pg_select("tenants", {"id": f"eq.{tenant_id}", "select": "house_id"})
    tenant = rows[0] if rows else None
    if not tenant:
        return jsonify({"error": "not_found"}), 404

    pg_delete("tenants", {"id": f"eq.{tenant_id}"})
    pg_update("houses", {"profile_complete": False}, {"id": f"eq.{tenant['house_id']}"})
    return jsonify({"status": "removed"})


# ---------- COMPLAINTS ----------
@app.route("/api/complaints", methods=["GET", "POST"])
def complaints():
    if request.method == "POST":
        data = request.get_json(force=True)
        pg_insert("complaints", {
            "tenant_id": data.get("tenant_id"),
            "type": data.get("type"),
            "message": data.get("message"),
            "status": "open",
        })
        return jsonify({"status": "ok"})

    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    rows = pg_select("complaints", {
        "select": "*,tenants(name,houses(block,house_num))",
        "order": "created_at.desc",
    })
    return jsonify(rows)


@app.route("/api/complaints/<int:complaint_id>/resolve", methods=["POST"])
def resolve_complaint(complaint_id):
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    pg_update("complaints", {"status": "resolved"}, {"id": f"eq.{complaint_id}"})
    return jsonify({"status": "ok"})


# ---------- TENANT-BY-TENANT: OWN COMPLAINTS ----------
@app.route("/api/tenant/<int:tenant_id>/complaints", methods=["GET"])
def tenant_complaints(tenant_id):
    rows = pg_select("complaints", {"tenant_id": f"eq.{tenant_id}", "order": "created_at.desc"})
    return jsonify(rows)


# ---------- SEMESTER RENEWAL INTEREST ----------
@app.route("/api/renew", methods=["POST"])
def renew():
    data = request.get_json(force=True)
    pg_insert("renewals", {
        "tenant_id": data.get("tenant_id"),
        "block": data.get("block"),
        "house_num": data.get("house_num"),
        "tenant_name": data.get("tenant_name"),
    })
    return jsonify({"status": "ok"})


@app.route("/api/renewals", methods=["GET"])
def list_renewals():
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    rows = pg_select("renewals", {"order": "created_at.desc"})
    return jsonify(rows)


# ---------- SETTINGS (estate name, theme colors, rent per block) ----------
@app.route("/api/settings", methods=["GET"])
def get_settings():
    rows = pg_select("settings", {"id": "eq.1"})
    return jsonify(rows[0] if rows else None)


@app.route("/api/settings", methods=["PUT"])
def update_settings():
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    data = request.get_json(force=True)

    updatable = {}
    for field in ("estate_name", "color_primary", "color_accent", "rent_a", "rent_b", "rent_c"):
        if field in data:
            updatable[field] = data[field]
    if updatable:
        pg_update("settings", updatable, {"id": "eq.1"})

    rent_map = {"A": data.get("rent_a"), "B": data.get("rent_b"), "C": data.get("rent_c")}
    for block, rent in rent_map.items():
        if rent is not None:
            pg_update("houses", {"rent": rent}, {"block": f"eq.{block}"})

    refreshed = pg_select("settings", {"id": "eq.1"})
    return jsonify(refreshed[0] if refreshed else None)


@app.route("/")
def health():
    return jsonify({"status": "VVIP Lounge backend running"})


@app.errorhandler(requests.exceptions.RequestException)
def handle_upstream_error(e):
    return jsonify({"error": "database_unreachable", "detail": str(e)}), 502


if __name__ == "__main__":
    app.run(debug=True)
