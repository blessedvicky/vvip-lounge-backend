import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if SUPABASE_URL else None


def require_admin(req):
    secret = req.args.get("secret") or req.headers.get("X-Admin-Secret")
    return ADMIN_SECRET is not None and secret == ADMIN_SECRET


def house_matches(block, house_num):
    return (
        sb.table("houses")
        .select("*, tenants(*)")
        .eq("block", block)
        .eq("house_num", house_num)
        .maybe_single()
        .execute()
        .data
    )


# ---------- HOUSES (landlord dashboard reads this) ----------
@app.route("/api/houses", methods=["GET"])
def list_houses():
    res = sb.table("houses").select("*, tenants(*)").order("block").order("house_num").execute()
    return jsonify(res.data)


# ---------- TENANT REGISTRATION (first-time sign-in) ----------
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(force=True)
    block = data.get("block")
    house_num = data.get("house_num")
    name = (data.get("name") or "").strip()

    if not name or block not in ("old", "new") or not house_num:
        return jsonify({"error": "missing_fields"}), 400

    house = house_matches(block, house_num)
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

    house = house_matches(block, house_num)
    if not house or not house.get("tenants"):
        return jsonify({"error": "not_found"}), 404

    tenant = house["tenants"][0] if isinstance(house["tenants"], list) else house["tenants"]
    if not tenant or tenant["name"].strip().lower() != name:
        return jsonify({"error": "name_mismatch"}), 401

    return jsonify({"tenant": tenant, "house": house})


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


if __name__ == "__main__":
    app.run(debug=True)
