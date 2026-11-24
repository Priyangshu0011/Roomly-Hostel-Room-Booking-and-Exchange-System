# Roomly — Hostel Room Exchange

Roomly provides an official, auditable workflow for hostel room exchanges:

- Students submit room preferences, subject to eligibility and block policy.
- The matching engine finds direct exchanges and bounded rotation cycles.
- Every resident in a proposal must accept before an authorised warden reviews it.
- Approval updates all room allocations in one PostgreSQL transaction and creates a transfer record.
- Hostel administrators configure room inventory and exchange rules; wardens are scoped to assigned blocks; Chief Wardens handle cross-block exchanges; Deans have read-only analytics access.

## Run locally with Docker Desktop

Requirements: Docker Desktop with Docker Compose enabled.

1. Copy the environment template and generate a local secret:

   ```powershell
   Copy-Item .env.example .env
   $key = python -c "import secrets; print(secrets.token_urlsafe(50))"
   (Get-Content .env -Raw).Replace('replace-with-a-unique-secret-key', $key) | Set-Content .env -NoNewline
   ```

2. Start PostgreSQL, Redis, Django, the Celery worker, and Celery Beat:

   ```powershell
   docker compose up --build -d
   docker compose exec web python manage.py createsuperuser
   ```

   Migrations and standard role permissions run when the web service starts.
   Visit <http://localhost:8000/>. The administrator portal is at
   <http://localhost:8000/admin/>.

3. In Admin, create hostel blocks and rooms, verify each room, assign student
   room allocations and eligibility, configure an active exchange policy, and
   assign wardens to their blocks. Add staff users to the appropriate role
   groups (`Wardens`, `Chief Wardens`, `Hostel Administrators`, or
   `Student Welfare Deans`).

Stop with `docker compose down`. The PostgreSQL volume persists. Use
`docker compose down -v` only when intentionally deleting local database data.

## Run tests

```powershell
docker compose exec web python manage.py test
```

The PostgreSQL concurrency test uses separate threads and is skipped on
databases without `SELECT ... FOR UPDATE` support.

## Deploy to Render

The included [render.yaml](./render.yaml) provisions the Django web service,
PostgreSQL, Redis, a Celery worker, and Celery Beat. Beat checks request and
proposal expiry and runs bounded matching on a periodic schedule. Review
service and database pricing in Render before creating the Blueprint.

After deployment, create an administrator from the Render Shell:

```sh
python manage.py createsuperuser
```

Then configure inventory and permissions in Django Admin as described above.
Never commit `.env` or production credentials. Vercel is not a drop-in host for
this Django/Celery application; Render or another Python host is recommended.

## Transaction and locking strategy

Exchange approval uses one `transaction.atomic()` block. Every affected room is
locked first in ascending primary-key order, followed by resident allocations
and exchange requests in the same deterministic order. The service revalidates
eligibility, request state, and projected room capacity while those locks are
held, then updates every resident, marks the proposal complete, and writes the
transfer and audit records before commit. If any validation or database write
fails, the transaction rolls back every allocation change. PostgreSQL is
required for the row-locking and concurrency guarantees; SQLite is suitable
only for limited local checks, not production exchange approval.

Room occupancy currently uses this application's `StudentProfile` allocation
records. A live P03 integration requires that system's API or data contract;
none was included with the downloaded project.

## JSON API

The same workflow is available to authenticated clients using Django session
authentication and CSRF protection:

- `GET` / `POST` `/exchange-requests/`
- `GET` `/proposals/`
- `POST` `/proposals/<id>/respond/` with `{"decision":"accept"}` or `decline`
- `POST` `/proposals/<id>/approve/` with a decision and required remarks
- `GET` `/transfers/` and `/transfers/<uuid>/`

The web interface uses the same service layer and authorization checks as these
endpoints.
