# VVIP Lounge Estate Ledger — backend

Same stack as the Prime Wear / Collective 254 backend: Flask + Supabase, deployed on Render.

## 1. Supabase (database)
1. Create a new Supabase project (or reuse an existing one — a new project keeps this separate).
2. Open the SQL editor and run everything in `schema.sql`. This creates the four tables and seeds the real 15 houses (6 old @ KSh 25,000, 9 new @ KSh 30,000).
3. From Project Settings → API, copy the **Project URL** and the **service_role secret key** (not the anon key — the backend needs write access).

## 2. GitHub
1. Create a new repo (e.g. `vvip-lounge-backend`) and push these files: `app.py`, `schema.sql`, `requirements.txt`, `README.md`.

## 3. Render (hosting)
1. New → Web Service → connect the repo.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:app`
4. Environment variables:
   - `SUPABASE_URL` — from step 1
   - `SUPABASE_SERVICE_KEY` — from step 1
   - `ADMIN_SECRET` — make up a password only you (and the landlord dashboard) know; this protects removing tenants, viewing complaints, and viewing renewal requests.
5. Deploy. Once live, note the base URL (e.g. `https://vvip-lounge-backend.onrender.com`) — visiting it should show `{"status": "VVIP Lounge backend running"}`.

Render's free tier cold-starts after inactivity, same as before — first request after idle can take 30–50s.

## Endpoints

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/houses` | landlord dashboard | list all 15 houses with tenant info |
| POST | `/api/register` | tenant portal, first-time | claim a house — 409 if already taken |
| POST | `/api/login` | tenant portal, returning | name + block + house_num |
| POST | `/api/tenant/<id>/remove?secret=...` | landlord dashboard | delete tenant, free the house |
| GET/POST | `/api/complaints` | both | list (admin) / submit (tenant) |
| POST | `/api/complaints/<id>/resolve?secret=...` | landlord dashboard | mark resolved |
| GET | `/api/tenant/<id>/complaints` | tenant portal | a tenant's own concerns |
| POST | `/api/renew` | tenant portal | express interest in keeping the house |
| GET | `/api/renewals?secret=...` | landlord dashboard | list renewal interest |

## Tested
`test_app.py` runs the full flow — register, duplicate-house conflict, login (match/mismatch), admin-gated removal, house re-opening after removal, complaint create + resolve, renewal interest — against an in-memory fake of the Supabase client. All 9 checks pass. Run locally with `python3 test_app.py` (needs `flask`, `flask-cors`, `supabase` installed).

## Next step
Once this is deployed and you have the base URL, send it over and I'll wire both `estate-dashboard.html` and `tenant-portal.html` to call it instead of using their current in-memory sample data — that's what makes the two dashboards actually talk to each other.
