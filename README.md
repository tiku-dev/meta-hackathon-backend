# University School Management System - Backend

Django REST Framework API implementing
[`school-management-system-blueprint.md`](../school-management-system-blueprint.md).

## Quickstart (Windows)

```powershell
cd backend
py -3.14 -m pip install -r requirements.txt   # Python 3.12+ also fine
copy .env.example .env                        # optional; defaults work
py -3.14 manage.py migrate
py -3.14 manage.py seed_demo                  # demo data + users
py -3.14 manage.py runserver
py -3.14 -m pytest                            # acceptance tests (blueprint SS11)
```

- API base: `http://127.0.0.1:8000/api/`
- Swagger UI: `http://127.0.0.1:8000/api/docs/`  | ReDoc: `/api/redoc/`
- Django admin: `http://127.0.0.1:8000/admin/` (create a superuser first)

## Demo accounts (seeded, password `DemoPass123!`)

| Email | Role |
|---|---|
| admin@demo.edu | admin |
| finance@demo.edu | finance |
| registrar@demo.edu | registrar |
| hod@demo.edu | HOD (Computer Science) |
| lecturer@demo.edu | lecturer |
| student@demo.edu | student (unpaid invoice) |

JWT login: `POST /api/auth/token/` with `{"email": ..., "password": ...}`
(email is the username). The seed also prints a prospect activation token;
activate via `POST /api/auth/activate` `{"token": ..., "password": ...}`.

## 5-minute demo flow (SS12)

1. Admin logs in -> `GET /api/admin/overview`; edits fee via
   `POST /api/admin/fee-schedules` (e.g. 75,000 XAF - nothing is hard-coded).
2. `POST /api/admin/students` returns a matricule + one-time activation token.
3. Student activates, checks `GET /api/student/fees`, then
   `POST /api/student/registrations` -> blocked `PAYMENT_THRESHOLD_NOT_MET`.
4. `POST /api/invoices/{id}/payments` -> pending attempt; simulate the
   provider confirming with `POST /api/payments/{reference}/mock-simulate`
   (sandbox only), then `GET /api/payments/{reference}/status`.
5. Registration now succeeds; `GET /api/timetable` shows sessions.
6. Lecturer `PUT /api/lecturer/offerings/{id}/results` (CA 24 + exam 56),
   HOD approves, registrar publishes, student sees grade A + GPA.

## Apps

| App | Responsibility |
|---|---|
| `core` | Audit log (append-only), error codes, role permissions, reports |
| `accounts` | Users/roles, student & lecturer profiles, matricules, activation tokens (SHA-256 hashed) |
| `academics` | Units/programs/levels/years/semesters, courses, offerings, atomic registration, timetable, attendance, exemptions |
| `finance` | Fee schedules, invoices, payments, receipts, `PaymentProvider` (mock / Notch Pay / Stripe-disabled), idempotent webhooks |
| `results` | Grading policies/bands, CA/final workflow draft -> submitted -> HOD-approved -> published/locked, corrections, GPA, transcript PDFs |
| `communication` | Announcements (audience-filtered), notifications, events + RSVP, deadline alerts |

## Key endpoints (SS8)

Auth: `POST /api/auth/token`, `POST /api/auth/activate`, `GET /api/me`,
`GET /api/student/me`
Admin: `/api/admin/students`, `/api/admin/fee-schedules`, `/api/admin/invoices`,
`/api/admin/semesters/{id}/open-registration`, `/api/admin/course-offerings`,
`/api/admin/offerings/{id}/publish-results`, `/api/admin/reports/{report}`
(`students|fee-balances|payments|registrations|results`, `?export=csv`),
`/api/admin/audit-logs`, `/api/admin/overview`, `/api/admin/payments/reconciliation`
Student: `/api/student/fees`, `/api/student/registration/available`,
`POST|GET|DELETE /api/student/registrations`, `/api/student/results`,
`/api/student/transcript`, `/api/student/receipts`
Payments: `POST /api/invoices/{id}/payments`, `GET /api/payments/{ref}/status`,
`POST /api/webhooks/notchpay`, `POST /api/webhooks/stripe`
Lecturer/HOD: `PUT /api/lecturer/offerings/{id}/results`,
`POST /api/lecturer/offerings/{id}/attendance`,
`POST /api/hod/offerings/{id}/approve-results`, `/api/hod/department/overview`

Business-rule failures return
`{"error": {"code": "PAYMENT_THRESHOLD_NOT_MET", "message": ..., "status": 400, "details": ...}}`
with stable codes: `REGISTRATION_CLOSED`, `PAYMENT_THRESHOLD_NOT_MET`,
`CREDIT_LIMIT_EXCEEDED`, `COURSE_FULL`, `ALREADY_REGISTERED`,
`TIMETABLE_CONFLICT`, `RESULTS_LOCKED`, `ACTIVATION_TOKEN_EXPIRED`, ...

## Payments - safety rules (SS9)

- Internal attempts are created **before** calling the provider; a browser
  redirect never marks anything paid.
- Credit is granted only after server-side verification (status endpoint or
  webhook -> provider `verify`), with reference/amount/currency matching;
  mismatches park the attempt in `needs_review`.
- Webhooks are deduplicated on `(provider, event_id)`; reconciling an
  already-succeeded attempt is a no-op (no double credit, one receipt).
- **Sandbox (current local setup):** `backend/.env` (never committed - see
  root `.gitignore`) holds the real *test* keys:
  `PAYMENT_PROVIDER=notchpay`, `PAYMENT_ALLOW_MOCK=0`, `pk_test/sk_test`
  Stripe keys and `pk_test./sk_test./hsk_test.` Notch Pay keys. Verify with
  `py -3.14 verify_sandbox.py` (auth-only, creates nothing).
- Notch Pay (default, XAF/Mobile Money): `POST /payments/initialize` +
  `GET /payments/{reference}`, authorized with the **public** key per the
  provider docs; the private key is only for high-risk endpoints. Webhook
  bodies are never trusted alone - every event is re-verified server-side;
  confirm the exact webhook-hash procedure with current provider docs
  before production.
- Stripe (test mode): Checkout Sessions are created server-side; select it
  per payment with `{"provider": "stripe"}` on
  `POST /api/invoices/{id}/payments`. Set `STRIPE_WEBHOOK_SECRET` from a
  test webhook endpoint / `stripe listen` for signed webhooks. XAF is
  zero-decimal (amounts are not multiplied by 100). **Live** Stripe still
  requires confirming merchant eligibility (blueprint SS9) - test keys only
  until then. `PAYMENT_PROVIDER=mock` remains a labelled fake for tests:
  never enable it outside tests/demo (`PAYMENT_ALLOW_MOCK=0` in production).

## Configuration, not code (SS4)

Fees, thresholds, deadlines, credit caps (level -> settings fallback),
CA/exam maxima, grade bands, GPA weighting and pass marks all live in DB
records (`FeeSchedule`, `Semester`, `Level`, `GradingPolicy`, `GradeBand`).
The seeded 60,000 XAF is a demo default only. Postgres:
`DATABASE_URL=postgres://user:pass@host:5432/db` (see docker-compose.yml).

## Notes / deliberate simplifications

- SQLite by default (atomicity via row locks works on both; use Postgres in
  production). RBAC is view-level + queryset scoping (no Postgres RLS).
- Emails use the console backend by default; activation responses include the
  token for the demo (`EMAIL_BACKEND` to change).
- Tests: `tests/test_api.py` maps to the SS11 acceptance criteria.
