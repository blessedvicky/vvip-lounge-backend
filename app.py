import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")

VALID_BLOCKS = {"A": 6, "B": 6, "C": 3}

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_URL else None


def require_admin(req):
    # Admin passcode travels as a request header only (never in the URL),
    # so it doesn't end up in browser history or server access logs.
    secret = req.headers.get("X-Admin-Secret")
    return ADMIN_SECRET is not None and secret == ADMIN_SECRET


def house_full(block, house_num):
    return (
        sb.table("houses")
        .select("*, tenants(*)")
        .eq("block", block)
        .eq("house_num", house_num)
        .maybe_single()
        .execute()
        .data
    )


# ---------- PUBLIC: occupancy only, no tenant details ----------
# Used by the tenant portal's house picker so anyone loading the page
# can see which houses are open, without exposing any tenant's name,
# phone, emergency contact, or photo.
@app.route("/api/houses", methods=["GET"])
def list_houses_public():
    res = (
        sb.table("houses")
        .select("id, block, house_num, rent, condition_notes, profile_complete")
        .order("block")
        .order("house_num")
        .execute()
    )
    return jsonify(res.data)


# ---------- ADMIN ONLY: full tenant details ----------
# Used by the landlord dashboard once the admin passcode has been entered.
@app.route("/api/houses/full", methods=["GET"])
def list_houses_full():
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    res = sb.table("houses").select("*, tenants(*)").order("block").order("house_num").execute()
    return jsonify(res.data)


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
    inserted = sb.table("tenants").insert(tenant).execute().data[0]
    sb.table("houses").update({"profile_complete": True}).eq("id", house["id"]).execute()
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
# Lightweight auth to match this app's simple model: the caller must supply
# the tenant's own name (same as their login), not just the id.
@app.route("/api/tenant/<int:tenant_id>", methods=["PUT"])
def update_tenant(tenant_id):
    data = request.get_json(force=True)
    confirm_name = (data.get("confirm_name") or "").strip().lower()

    existing = sb.table("tenants").select("*").eq("id", tenant_id).maybe_single().execute().data
    if not existing:
        return jsonify({"error": "not_found"}), 404
    if existing["name"].strip().lower() != confirm_name:
        return jsonify({"error": "name_mismatch"}), 401

    updatable = {}
    for field in ("course", "phone", "emergency_name", "emergency_phone", "roommates", "photo_url"):
        if field in data:
            updatable[field] = data[field]
    if updatable:
        sb.table("tenants").update(updatable).eq("id", tenant_id).execute()

    refreshed = sb.table("tenants").select("*").eq("id", tenant_id).maybe_single().execute().data
    return jsonify({"tenant": refreshed})


# ---------- LANDLORD: REMOVE A TENANT (frees the house on both sides) ----------
@app.route("/api/tenant/<int:tenant_id>/remove", methods=["POST"])
def remove_tenant(tenant_id):
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403

    tenant = sb.table("tenants").select("house_id").eq("id", tenant_id).maybe_single().execute().data
    if not tenant:
        return jsonify({"error": "not_found"}), 404

    sb.table("tenants").delete().eq("id", tenant_id).execute()
    sb.table("houses").update({"profile_complete": False}).eq("id", tenant["house_id"]).execute()
    return jsonify({"status": "removed"})


# ---------- COMPLAINTS ----------
@app.route("/api/complaints", methods=["GET", "POST"])
def complaints():
    if request.method == "POST":
        data = request.get_json(force=True)
        sb.table("complaints").insert({
            "tenant_id": data.get("tenant_id"),
            "type": data.get("type"),
            "message": data.get("message"),
            "status": "open",
        }).execute()
        return jsonify({"status": "ok"})

    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    res = (
        sb.table("complaints")
        .select("*, tenants(name, houses(block, house_num))")
        .order("created_at", desc=True)
        .execute()
    )
    return jsonify(res.data)


@app.route("/api/complaints/<int:complaint_id>/resolve", methods=["POST"])
def resolve_complaint(complaint_id):
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    sb.table("complaints").update({"status": "resolved"}).eq("id", complaint_id).execute()
    return jsonify({"status": "ok"})


# ---------- TENANT-BY-TENANT: OWN COMPLAINTS ----------
@app.route("/api/tenant/<int:tenant_id>/complaints", methods=["GET"])
def tenant_complaints(tenant_id):
    res = (
        sb.table("complaints")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
    )
    return jsonify(res.data)


# ---------- SEMESTER RENEWAL INTEREST ----------
@app.route("/api/renew", methods=["POST"])
def renew():
    data = request.get_json(force=True)
    sb.table("renewals").insert({
        "tenant_id": data.get("tenant_id"),
        "block": data.get("block"),
        "house_num": data.get("house_num"),
        "tenant_name": data.get("tenant_name"),
    }).execute()
    return jsonify({"status": "ok"})


@app.route("/api/renewals", methods=["GET"])
def list_renewals():
    if not require_admin(request):
        return jsonify({"error": "unauthorized"}), 403
    res = sb.table("renewals").select("*").order("created_at", desc=True).execute()
    return jsonify(res.data)


@app.route("/")
def health():
    return jsonify({"status": "VVIP Lounge backend running"})


# ---------- SETTINGS (estate name, theme colors, rent per block) ----------
@app.route("/api/settings", methods=["GET"])
def get_settings():
    res = sb.table("settings").select("*").eq("id", 1).maybe_single().execute()
    return jsonify(res.data)


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
        sb.table("settings").update(updatable).eq("id", 1).execute()

    # Keep each block's house rows in sync with the new rent so the public
    # /api/houses (and registration) reflect it immediately.
    rent_map = {"A": data.get("rent_a"), "B": data.get("rent_b"), "C": data.get("rent_c")}
    for block, rent in rent_map.items():
        if rent is not None:
            sb.table("houses").update({"rent": rent}).eq("block", block).execute()

    refreshed = sb.table("settings").select("*").eq("id", 1).maybe_single().execute()
    return jsonify(refreshed.data)


if __name__ == "__main__":
    app.run(debug=True)
