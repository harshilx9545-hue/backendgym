# Implementation Plan: gym-saas-core

## Overview

Phase 1 grows the existing Django project in place: one project package (`gymapp`), one app (`core`).
The order below is bottom-up so nothing depends on code that does not exist yet: dependencies and the
`Config_Loader` first, then pure services (money, slugs, validators), then the full model layer, then the
destructive migration-baseline rebuild, then the tenant isolation and authorization layer, then auth,
then the money services, then the gateway and webhook, then serializers/views/URL wiring, and finally the
two conformance management commands that act as deployment gates.

Language: Python 3 / Django, as specified in the design. Tests use `pytest` + `pytest-django` +
`hypothesis`, with a `FakeRazorpayAdapter` so no property test touches the network.

Property test tasks reference the 40 correctness properties in `design.md` by number. Each property is
implemented by exactly one property-based test in its own module, at `@settings(max_examples=100)` or 500
for the pure functions (Properties 7, 18, 19, 27, 28).

## Current status

**All tasks complete.**

| Backend | Result |
| --- | --- |
| SQLite (default) | 323 passed, 3 skipped, 4 deselected |
| PostgreSQL 16 | 326 passed, **0 skipped**, 4 deselected |
| SQLite under `-n auto` | 323 passed, 3 skipped |

The 3 SQLite skips are the concurrency clauses, which run and pass on PostgreSQL — see
below. The 4 deselected are the Razorpay sandbox tests, excluded by `pytest.ini`.

All 40 design properties now have exactly one property-based test each. Deployment
gates: `manage.py check`, `check --deploy` against a production-shaped environment,
`check_tenant_scoping`, `check_api_surface` and `makemigrations --check --dry-run` all
pass, and `migrate` applies cleanly to an empty database from a single baseline.

### How to run

```bash
pip install -r requirements.txt
pytest -q                       # gateway integration tests are excluded by pytest.ini
pytest -q -m integration        # opt in to the Razorpay sandbox tests
python manage.py check_tenant_scoping
python manage.py check_api_surface
```

A `.env` is required — copy `.env.example` and set at least `DJANGO_SECRET_KEY`.
`gymapp/config.validate()` runs as the last statement of `settings.py`, so a missing
key raises `ImproperlyConfigured` at import rather than failing later.

Under pytest, `PASSWORD_HASHERS` drops to MD5 (guarded by `"pytest" in sys.modules`
**and** `DEBUG`). That is a test-speed measure only; the `DEBUG` conjunct is the
fail-safe so a misfire on a production process cannot weaken password storage.

### The three PostgreSQL-only tests

- `test_property_20_invoice_numbering.py::test_concurrent_issues_produce_no_duplicates`
- `test_property_23_webhook_idempotency.py::test_concurrent_deliveries_of_one_event_settle_once`
- `test_property_32_concurrent_members.py::test_concurrent_attempts_yield_exactly_min_n_k`

These skip unless the test database is PostgreSQL, which is the design's own position:
SQLite takes a database-level lock rather than a row-level one, so `select_for_update()`
cannot be shown to do anything there and a passing test would prove nothing. Each module
also carries a sequential variant that runs on SQLite and covers the arithmetic
(`min(N, K)`, gapless numbering, one terminal Payment per event).

All three have been executed and pass against PostgreSQL 16:

```bash
DATABASE_URL=postgres://user:pw@localhost:5432/gymapp pytest -q
# 326 passed, 4 deselected  (no skips)
```

### Implementation defects found and fixed while writing the remaining tests

| Area | Defect | Requirement |
| --- | --- | --- |
| `core/scoping.py` | `TenantScopedQuerysetMixin` called `super().get_queryset()`, which does not exist on a plain `APIView`. `POST /api/invoices/{id}/pay` and `GET /api/payments/{id}/receipt` both raised `AttributeError` and returned 500 — the whole payment path was unreachable. Added `get_base_queryset()`. | 17.1, 19.9 |
| `core/models.py` | Soft-deleting a Payment, Invoice or Membership wrote no `AuditRecord`. Added an opt-in audit on the soft-delete/restore path. | 22.1 |
| `core/views/profiles.py` | `MeView` carried no write gate, so a lapsed Gym and an inactive Member could still `PATCH /api/me`. Added `RoleAllowed`, `SubscriptionWriteGate`, `ActiveMemberGate`. | 20.8, 21.5 |
| `core/views/profiles.py` | Member creation answered 403 rather than the mandated 402 when the Gym had no live subscription, because `SubscriptionWriteGate` pre-empted `RequiresSubscription`. Reordered. | 5.7 |
| `core/serializers.py` | A duplicate MembershipPlan name reached the `UniqueConstraint(Lower("name"))` index and surfaced as a 500; DRF cannot derive a validator from a constraint containing an expression. Added `validate_name`. | 2.4 |
| `core/serializers.py` | A duplicate phone on a trainer or member invite surfaced as a 500. Added `validate_phone` to both invite serializers. | 10.3 |
| `core/models.py` | `unique_together` does not constrain rows where `gym` is NULL, so the platform-wide `StrengthStandard` key was not actually unique and the shared-row lookup could return two contradictory rows. Added a partial `UniqueConstraint`. | 2.5, 2.2 |
| `core/views/profiles.py` | Trainer and member lists paginated an unordered queryset, which can repeat or drop a row between pages. Added explicit ordering. | — |
| `tools/rebuild_migration_baseline.py` | `rebuild()` swept the module-level `MIGRATIONS_DIR` rather than the baseline's own directory, so a caller that redirected `BASELINE` still deleted the real migrations. Now derived from `BASELINE.parent`. | 9.2 |

### Migration baseline note

Migration `0002_alter_memberprofile_options.py` no longer exists. Task 5.2 regenerated
the baseline, and requirement 9.1 asks for a *single* replacement migration, so the
rebuild script now sweeps every generated migration before regenerating. The tree holds
exactly `core/migrations/0001_initial.py` plus the package marker, and
`makemigrations --check --dry-run` reports nothing pending.

## Tasks

- [x] 1. Dependencies and test scaffolding
  - [x] 1.1 Update `requirements.txt` with the Phase 1 dependency set
    - Correct the Django pin to the targeted major version (`Django>=5.2,<6.0` or the version the code is verified against) so the declared range and the target agree
    - Add `djangorestframework`, `djangorestframework-simplejwt`, `razorpay`, `django-environ`, `psycopg[binary]`
    - Add test dependencies: `pytest`, `pytest-django`, `pytest-xdist`, `hypothesis[django]`, `freezegun`
    - Keep the existing deferred-phase entries (`django-htmx`, `cloudinary`, `celery`, `redis`, `twilio`) untouched
    - _Requirements: 6.13, 24.1_

  - [x] 1.2 Add pytest configuration and the test package skeleton
    - Create `pytest.ini` with `DJANGO_SETTINGS_MODULE`, test discovery paths, and an `integration` marker registered so gateway tests can be excluded from the default run
    - Replace the empty `core/tests.py` with a `core/tests/` package (`__init__.py`, `conftest.py`)
    - Provide fixtures for a settings-override hook used later to inject the fake gateway
    - _Requirements: 24.1_

  - [x] 1.3 Add pure-value hypothesis strategies
    - Create `core/tests/strategies.py` with `two_dp_decimals()`, `e164_phones()`, `unicode_business_names()`, `iana_timezones()`, `membership_periods()`
    - Include the edge cases the design calls for: empty and whitespace strings, names that transliterate to empty, `duration_days` at 0/1/3650/3651, amounts at 0.00/0.01, leap-year and DST-adjacent dates
    - _Requirements: 17.4, 20.2, 20.3, 10.3, 1.9_

- [x] 2. Config_Loader, settings, and app configuration
  - [x] 2.1 Implement `gymapp/config.py`
    - Typed readers `env_str`, `env_bool`, `env_int`, `env_list`, `require`, plus `is_valid_host`
    - `validate(settings_dict)` performing every startup check: missing `SECRET_KEY`, `DEBUG` default false, empty `ALLOWED_HOSTS` under production, wildcard or malformed host entries, `django-insecure-` key under production, missing `EMAIL_BACKEND` under production, and every absent SMTP variable named together
    - Raise `ImproperlyConfigured` so a violated check prevents startup rather than degrading a component
    - _Requirements: 6.1, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 8.2, 8.3, 8.4, 8.5_

  - [x] 2.2 Rewrite `gymapp/settings.py` to consume the Config_Loader
    - Remove the hardcoded `SECRET_KEY` and the `MAILERS` block; read every environment-specific value through `config.py`; call `validate()` as the final statement
    - Add `DATABASE_URL`-driven SQLite/PostgreSQL selection, `STATIC_ROOT`, `MEDIA_ROOT`, `MEDIA_URL`, and the `LOGGING` block writing `INFO`+ to stdout with named `core.payments` and `core.auth` loggers
    - Add the DRF block (default permission class, exception handler, throttle classes) and the simplejwt block with env-driven lifetimes, `ROTATE_REFRESH_TOKENS`, `BLACKLIST_AFTER_ROTATION`
    - Apply production hardening under `not DEBUG` (SSL redirect, secure cookies, `SECURE_HSTS_SECONDS = 31536000`, `SECURE_PROXY_SSL_HEADER`) and nosniff plus `X_FRAME_OPTIONS = "DENY"` unconditionally
    - _Requirements: 6.1, 6.2, 6.9, 6.10, 6.12, 7.1, 7.2, 7.3, 8.1, 8.2, 13.3, 24.1_

  - [x] 2.3 Create `.env.example`
    - List every variable name the Config_Loader reads, with placeholder values and no real secrets
    - _Requirements: 6.11_

  - [x] 2.4 Declare `default_auto_field` on `CoreConfig`
    - Set `default_auto_field = "django.db.models.BigAutoField"` in `core/apps.py`
    - _Requirements: 7.5_

  - [x]* 2.5 Write property test for configuration completeness
    - **Property 40: Missing SMTP configuration is reported completely**
    - **Validates: Requirements 8.5, 6.6, 6.7**

  - [x]* 2.6 Write unit tests for configuration defaults and settings shape
    - Missing `SECRET_KEY` error, `DEBUG` default false, empty `ALLOWED_HOSTS` error, console backend under debug, missing `EMAIL_BACKEND` error under production
    - Assert `MAILERS` absent, no `SECRET_KEY` literal in tracked source, `.env.example` covers every variable the loader reads, security and logging settings present
    - _Requirements: 6.2, 6.3, 6.4, 6.5, 6.9, 6.10, 6.11, 6.13, 7.1, 7.2, 7.3, 8.1, 8.3, 8.4_

- [x] 3. Validators and pure services
  - [x] 3.1 Implement `core/validators.py`
    - E.164 phone (`^\+[1-9]\d{7,14}$`), gym slug (`^[a-z0-9-]+$`), IANA timezone against `zoneinfo.available_timezones()`, GSTIN, ISO-4217 currency code
    - _Requirements: 1.1, 10.3, 16.8, 2.2_

  - [x] 3.2 Implement `core/services/money.py`
    - `to_minor_units` with `ROUND_HALF_UP` on 2 decimal places and `from_minor_units`, paise for INR
    - _Requirements: 17.3, 17.4_

  - [x] 3.3 Implement `core/services/slugs.py`
    - Transliterate to ASCII, lowercase, collapse non-alphanumeric runs to a single hyphen, strip edge hyphens, truncate to 60 characters, fall back to the literal `gym`
    - Case-insensitive collision suffixing from 2 upward for at most 50 attempts, truncating the base so each candidate stays within 60 characters; signal exhaustion so the caller can reject the registration
    - _Requirements: 1.9, 1.10, 1.11, 1.12, 1.4_

  - [x]* 3.4 Write property test for slug derivation
    - **Property 7: Slug derivation is well-formed and collision-free**
    - **Validates: Requirements 1.9, 1.10, 1.11, 1.4**

  - [x]* 3.5 Write property test for money conversion
    - **Property 18: Minor-unit conversion round-trips**
    - **Validates: Requirements 17.4, 17.3**

- [x] 4. Data model layer
  - [x] 4.1 Implement `core/managers.py`
    - Email-keyed `UserManager` with `create_user`/`create_superuser` (no username requirement)
    - `SoftDeleteManager` excluding `deleted_at IS NOT NULL` from default querysets, with an explicit all-records manager for owner reports that request deleted rows
    - An append-only manager for AuditRecord that raises on `update()` and `delete()`
    - _Requirements: 10.1, 10.9, 11.2, 22.2, 22.4_

  - [x] 4.2 Rewrite the identity model in `core/models.py`
    - `USERNAME_FIELD = "email"`, unique non-empty email, `username` optional and off the login path, `phone` nullable and unique with the E.164 validator, `role` default `member`, new `email_verified` boolean, wire the custom manager
    - _Requirements: 10.1, 10.2, 10.3, 10.9, 11.1_

  - [x] 4.3 Add the Gym model and scope the existing domain models to it
    - `Gym` with name, validated slug (`UniqueConstraint(Lower("slug"))`), contact email, E.164 contact phone, IANA timezone, nullable GSTIN, immutable `created_at`, `is_active` default true
    - Non-nullable `gym` FK plus `deleted_at` on `OwnerProfile`, `TrainerProfile`, `MemberProfile`; non-nullable `gym` FK on `Equipment`
    - Rename `Plan` to `MembershipPlan`, drop `max_members_allowed`, add `gym` FK, bound `duration_days` to 1–3650, add `currency`, add `UniqueConstraint("gym", Lower("name"))`
    - `StrengthStandard` gains a nullable `gym` FK with `unique_together` on `("gym", "exercise_name", "gender")`
    - `MemberProfile`: retarget `plan` to `MembershipPlan`, remove the stored `status` field, make `join_date` an explicit settable `DateField`
    - _Requirements: 1.1, 1.3, 1.6, 1.13, 2.1, 2.2, 2.4, 2.5, 4.2, 4.3, 4.4, 4.5, 20.4, 20.9_

  - [x] 4.4 Add the plan, subscription, and membership models
    - `SaasPlan`: name, price `Decimal(12,2) >= 0`, currency, `billing_interval_months >= 1`, nullable `max_members_allowed` bounded 1–100000 when non-null; not Gym-scoped
    - `SaasSubscription`: OneToOne `gym`, `plan`, `start_date`, `current_period_end`, status choices `trialing`/`active`/`past_due`/`cancelled`, nullable gateway reference
    - `Membership`: `member`, `plan`, `start_date`, computed `end_date`, `deleted_at`, no stored status, `CheckConstraint(end_date >= start_date)`
    - _Requirements: 4.1, 4.5, 5.3, 20.1, 20.2, 21.1, 21.2_

  - [x] 4.5 Add the financial models and replace `Payment`
    - `Invoice` with number, `payer_user`, `gym`, nullable `saas_subscription` and `membership`, `taxable_value`, nullable `cgst`/`sgst`/`igst`, nullable `hsn_sac`, `total_amount`, currency, status choices, `issue_date`, `due_date`, `financial_year`, `sequence_no`, `deleted_at`, and the two uniqueness constraints
    - `InvoiceSequence` unique on `(gym, financial_year)`; `CreditNote` with invoice, number, amount, reason, issue date
    - Replace `Payment`: invoice FK, gym FK, `amount` `Decimal(12,2)` with `CheckConstraint(amount >= 0.01)`, ISO-4217 currency defaulting to `INR`, status choices `pending`/`succeeded`/`failed`/`refunded`/`cancelled`, gateway name, unique-when-present `gateway_payment_ref`, `gateway_order_ref`, unique `idempotency_key`, nullable method, `paid_at`, `created_at`, explicit settable `recorded_on`, self-FK `refund_of`, `deleted_at`
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.7, 16.8, 19.1, 19.2, 22.3_

  - [x] 4.6 Add the supporting records
    - `WebhookEvent` with unique `event_id`, kind, card-data-stripped `raw_payload`, `received_at`, nullable `processed_at`, nullable `matched_payment`, `reconciliation_required`
    - `AuditRecord` with nullable actor, action, model label, object id, nullable gym, `changes` JSON, `created_at`, using the append-only manager
    - `EmailVerificationToken` and `PasswordResetToken` storing only token hashes, with `expires_at` and `consumed_at`
    - _Requirements: 14.3, 18.7, 18.9, 22.1, 22.2_

  - [x]* 4.7 Write unit tests for model shape and schema
    - Field presence, choice sets, constraints, non-nullable Gym FKs, `max_members_allowed` only on `SaasPlan`, and no card-data field on any model
    - _Requirements: 1.1, 1.3, 2.1, 4.1, 4.2, 4.3, 16.1, 16.2, 19.1, 19.2, 21.1, 21.2, 23.1_

- [x] 5. Destructive migration-baseline rebuild
  - [x] 5.1 Write the single-step baseline rebuild script
    - Create `tools/rebuild_migration_baseline.py` that deletes `db.sqlite3` and `core/migrations/0001_initial.py`, verifies both deletions succeeded, and only then invokes `makemigrations core`
    - If the migration-file deletion fails, abort before generating anything so replacement is one indivisible step
    - Preserve `core/migrations/__init__.py`
    - _Requirements: 9.1, 9.2_

  - [x] 5.2 Run the rebuild and verify the regenerated baseline
    - Execute the script to produce one clean `core/migrations/0001_initial.py`
    - Confirm the baseline declares every Gym FK on `MembershipPlan`, `Equipment`, `MemberProfile`, `TrainerProfile` as non-nullable with no backfill operation, and declares the User model with email as the login identifier
    - Run `manage.py migrate` against the empty database and `manage.py makemigrations --check --dry-run`, fixing model definitions until both report clean
    - _Requirements: 9.1, 9.3, 9.4, 9.5, 9.6_

  - [x]* 5.3 Write unit test for rebuild-script atomicity
    - Assert that a simulated failure to delete `0001_initial.py` produces no generated migration
    - _Requirements: 9.2_

- [x] 6. Checkpoint - models and baseline
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Tenant isolation and authorization layer
  - [x] 7.1 Add model factories and model-backed strategies
    - `core/tests/factories.py` for Gym, User per role with profile, SaasPlan, SaasSubscription, MembershipPlan, Membership, Invoice, Payment
    - Extend `core/tests/strategies.py` with `gyms()`, `users(role=...)`, `tenant_scoped_endpoints()`
    - _Requirements: 3.1, 12.4_

  - [x] 7.2 Implement the uniform error envelope
    - `core/exceptions.py` with `api_exception_handler` producing `{"error": {"code", "message", "details"}}` and the full code catalogue, including the custom `Http402`, `Http409`, `GatewayError` exception types
    - Register it as DRF's `EXCEPTION_HANDLER`
    - _Requirements: 24.9_

  - [x] 7.3 Implement `core/scoping.py`
    - `RequestContext` resolving user, profile, gym, role, subscription status, and active-member state from the database and caching on `request.tenant_context`
    - `TenantScopedQuerysetMixin` as the single filtering component, filtering on `gym=ctx.gym` and `Q(gym=ctx.gym) | Q(gym__isnull=True)` for `StrengthStandard`, ignoring `is_staff`/`is_superuser`, so detail lookups for another Gym raise `Http404` naturally
    - `NON_TENANT_VIEWS` registry holding exactly the nine allowlisted endpoint groups
    - _Requirements: 3.1, 3.2, 3.3, 3.5, 3.6, 3.7, 3.8, 2.2_

  - [x] 7.4 Implement `core/permissions.py`
    - `IsAuthenticatedWithProfile`, `RoleAllowed` with default deny for views declaring nothing, `MemberSelfScope`, `TrainerScope`, `OwnerScope`, `SubscriptionWriteGate`, `ActiveMemberGate`
    - Derive role and Gym from the database row, never from token claims
    - _Requirements: 1.7, 3.6, 13.8, 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9, 15.10, 15.11, 20.8, 21.5_

  - [x] 7.5 Implement `core/throttling.py`
    - Env-driven scoped throttles for login, registration, and password reset, clamped to a floor of 5 requests per minute
    - _Requirements: 24.7, 24.8_

  - [x]* 7.6 Write property test for access gates
    - **Property 3: Access gates deny before any data is read**
    - **Validates: Requirements 3.6, 1.7, 15.5, 15.1**

  - [x]* 7.7 Write property test for the error envelope
    - **Property 17: Uniform error envelope**
    - **Validates: Requirements 24.9, 11.1, 16.2, 19.2, 21.2**

  - [x]* 7.8 Write property test for rate limiting
    - **Property 37: Rate limit clamping and enforcement**
    - **Validates: Requirements 24.8, 24.7**

  - [x]* 7.9 Write unit test for default denial
    - Register a view with no permission declaration and assert 401 unauthenticated and 403 authenticated
    - _Requirements: 15.6_

- [x] 8. Auth service, registration, and authentication endpoints
  - [x] 8.1 Implement `core/services/auth_tokens.py`
    - `authenticate_identifier` with a case-insensitive email branch and a phone branch that validates E.164 before any database comparison
    - `issue_tokens`, `revoke_refresh`, `revoke_all_refresh`, `issue_email_token` (72h), `issue_reset_token` (60m), `consume_token`
    - Access token claims carry user id, role, and gym id
    - _Requirements: 10.4, 10.5, 10.6, 10.7, 10.8, 13.1, 13.2, 13.5, 13.6, 13.7, 14.3, 14.5_

  - [x] 8.2 Implement `core/services/email.py`
    - Non-blocking send helper that catches transport errors, logs recipient and message type on the `core.auth` logger, and lets the originating operation succeed
    - _Requirements: 8.6_

  - [x] 8.3 Implement `core/services/audit.py`
    - AuditRecord writer capturing actor, action, model label, object id, gym, and before/after values for exactly the changed fields
    - _Requirements: 22.1, 22.2, 3.10_

  - [x] 8.4 Implement registration and role/profile consistency services
    - `core/services/registration.py`: create Gym, User with role `owner`, and OwnerProfile in one `transaction.atomic()` block, deriving the slug and rejecting the request when suffixing is exhausted; ignore any client-supplied role or gym
    - Profile consistency rules: at most one non-soft-deleted profile per User, profile type must match role, no profile for staff accounts, role change blocked while a profile of the previous type exists, role change plus profile creation atomic when no such profile exists
    - Reject self-service registration for `trainer` and `member`; invited users inherit the inviting owner's Gym
    - _Requirements: 1.2, 1.8, 1.12, 11.3, 11.4, 11.5, 11.6, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_

  - [x] 8.5 Implement `core/views/auth.py` and the non-tenant auth routes
    - Owner registration, login, refresh, logout, email verification, password-reset request (always 202), password-reset confirmation (explicit 400 token errors), applying the configured password validators and the throttles
    - Distinct machine-readable codes for expired versus invalid tokens and for auth-service failure versus rejected credentials; register each view in `NON_TENANT_VIEWS`
    - _Requirements: 10.9, 13.4, 13.5, 13.6, 14.1, 14.2, 14.4, 14.6, 14.7, 14.8, 24.2_

  - [x]* 8.6 Write property test for registration atomicity
    - **Property 8: Registration is all-or-nothing**
    - **Validates: Requirements 1.2, 1.8, 1.6, 1.13, 1.12**

  - [x]* 8.7 Write property test for dual-identifier login
    - **Property 9: Login succeeds for either identifier**
    - **Validates: Requirements 10.4, 10.5, 13.1**

  - [x]* 8.8 Write property test for credential-failure indistinguishability
    - **Property 10: Credential failures are indistinguishable**
    - **Validates: Requirements 10.6, 10.7**

  - [x]* 8.9 Write property test for identifier validation and uniqueness
    - **Property 11: Identifier validation and uniqueness**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.9, 10.10**

  - [x]* 8.10 Write property test for access token claims
    - **Property 12: Access token claims round-trip**
    - **Validates: Requirements 13.7, 13.2**

  - [x]* 8.11 Write property test for the token lifecycle
    - **Property 13: Token lifecycle — rotation, revocation, single use, expiry**
    - **Validates: Requirements 13.5, 13.6, 14.1, 14.2, 14.3, 14.5, 14.6**

  - [x]* 8.12 Write stateful property test for role and profile agreement
    - **Property 16: Role and profile always agree**
    - **Validates: Requirements 12.4, 12.1, 12.2, 12.3, 12.5, 12.6, 12.7, 11.1, 11.3, 11.4, 11.5**

  - [x]* 8.13 Write property test for optional email failures
    - **Property 39: Optional email failures do not fail the operation**
    - **Validates: Requirements 8.6, 14.8, 14.1**

  - [x]* 8.14 Write unit tests for auth edge behaviour
    - `createsuperuser` produces a role within the choice set, expiry versus signature codes differ, reset request and reset confirmation return the contrasting documented shapes
    - _Requirements: 11.2, 13.4, 14.7_

- [x] 9. Checkpoint - isolation and auth
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Seat accounting and membership lifecycle
  - [x] 10.1 Implement `core/services/seats.py`
    - `seat_count` over non-soft-deleted MemberProfile rows; `create_member_atomically` locking the Gym row, checking subscription status (402) then the seat limit (409) and creating User plus MemberProfile in one transaction
    - `assert_plan_change_allowed` and `restore_member` with the 409 refusals; no seat evaluation on updates to existing MemberProfile rows
    - _Requirements: 5.1, 5.2, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10_

  - [x] 10.2 Implement `core/services/memberships.py`
    - `end_date_for`, `status_of`, `is_member_active`, `next_start_date`, `create_membership` with overlap and duration validation, all dates evaluated in the Gym's timezone
    - Zero-price plans create a Membership with no Invoice; priced plans create a Membership plus an Invoice; plan switch during an active Membership starts the day after the existing end date with no proration
    - _Requirements: 4.6, 4.7, 20.1, 20.2, 20.3, 20.5, 20.6, 20.7, 20.11, 20.12_

  - [x]* 10.3 Write property test for end-date arithmetic
    - **Property 27: Membership end date arithmetic**
    - **Validates: Requirements 20.2, 20.3, 4.4**

  - [x]* 10.4 Write property test for status classification
    - **Property 28: Membership status classification**
    - **Validates: Requirements 20.1**

  - [x]* 10.5 Write property test for active-iff-paid
    - **Property 29: A Member is active exactly when paid and in period**
    - **Validates: Requirements 20.5, 20.4, 20.9, 20.10**

  - [x]* 10.6 Write stateful property test for non-overlap and renewal chaining
    - **Property 30: Memberships never overlap and renewals chain**
    - **Validates: Requirements 20.11, 20.6, 20.7, 20.12, 4.6, 4.7**

  - [x]* 10.7 Write stateful property test for the seat invariant
    - **Property 31: Seat count never exceeds the plan limit**
    - **Validates: Requirements 5.4, 5.3**

  - [x]* 10.8 Write property test for concurrent member creation
    - **Property 32: Concurrent member creation respects remaining seats**
    - **Validates: Requirements 5.1**

  - [x]* 10.9 Write property test for gate applicability
    - **Property 33: Seat and subscription gates apply only to Seat_Count increases**
    - **Validates: Requirements 5.2, 5.7, 5.10, 5.8, 5.9**

  - [x]* 10.10 Write property test for plan changes
    - **Property 34: Plan changes respect the current seat count**
    - **Validates: Requirements 5.5, 5.6**

- [x] 11. Invoicing, GST, and financial integrity
  - [x] 11.1 Implement `core/services/invoicing.py`
    - `next_invoice_number` locking the `InvoiceSequence` row and formatting `{slug}/{fy}/{n:05d}` with the April–March financial year
    - `compute_tax` returning all-null fields without a GSTIN, CGST+SGST intra-state and IGST inter-state with one; `issue_invoice` setting total as taxable plus populated components; `settle` chaining the Membership and writing an AuditRecord; `void_via_credit_note`
    - _Requirements: 19.3, 19.4, 19.5, 19.6, 19.8, 4.6, 4.7_

  - [x] 11.2 Enforce financial immutability, soft delete, and audit coverage
    - Refuse amount, taxable-value, and tax-field changes on a `settled` Invoice with 409; refuse a `succeeded` → `pending` Payment transition; set `paid_at` on transition to `succeeded`
    - Route Payment, Invoice, and Membership deletion through soft delete with no hard-delete path; model refunds as a new `refunded` Payment referencing the original; write an AuditRecord for every create and modify
    - _Requirements: 16.9, 19.7, 22.1, 22.2, 22.3, 22.4, 22.6, 22.7_

  - [x]* 11.3 Write property test for invoice totals and GST
    - **Property 19: Invoice total equals taxable value plus populated tax**
    - **Validates: Requirements 19.6, 19.4, 19.5**

  - [x]* 11.4 Write property test for invoice numbering
    - **Property 20: Invoice numbers are unique and gapless per Gym and financial year**
    - **Validates: Requirements 19.3**

  - [x]* 11.5 Write property test for payment amount and currency validation
    - **Property 21: Payment amount and currency validation**
    - **Validates: Requirements 16.5, 16.6, 16.8**

  - [x]* 11.6 Write stateful property test for immutability and ledger conservation
    - **Property 25: Settled financial records are immutable and the ledger balances**
    - **Validates: Requirements 22.5, 22.6, 22.7, 22.1, 22.2, 22.3, 22.4, 19.7, 19.8, 16.9, 16.7, 19.9, 3.10**

- [x] 12. SaaS subscription billing
  - [x] 12.1 Implement `core/services/subscriptions.py`
    - Create a `trialing` subscription with the configured trial length at Gym creation; advance the stored period end by the billing interval and set `active` on SaaS Invoice settlement; derive `past_due` after a period end with no settled next-period Invoice; issue the SaaS Invoice at the configured lead time before period end
    - _Requirements: 21.3, 21.4, 21.6, 21.7_

  - [x]* 12.2 Write property test for subscription period arithmetic
    - **Property 36: Subscription period arithmetic**
    - **Validates: Requirements 21.3, 21.4, 21.6, 21.7**

- [x] 13. Razorpay adapter, order creation, and webhook handling
  - [x] 13.1 Implement `core/services/gateway.py`
    - `RazorpayAdapter.create_order` raising `GatewayError` and `CurrencyMismatch`, `verify_webhook` performing HMAC-SHA256 verification before any parsing, `parse_event` returning the frozen `WebhookEventData`
    - Frozen `GatewayOrder` dataclass; read the secret key and webhook secret from the environment and keep both out of responses and logs
    - _Requirements: 17.1, 17.3, 17.8, 18.2, 23.3, 23.5_

  - [x] 13.2 Implement the fake gateway for tests
    - `FakeRazorpayAdapter` matching the adapter interface with injectable failure modes, plus a `gateway_events()` strategy, wired through a settings override so no test hits the network
    - _Requirements: 17.6, 18.2_

  - [x] 13.3 Implement the order-creation flow
    - Service plus `POST /api/invoices/{id}/pay`: reject card-data field names at any nesting depth with 400, refuse an Invoice already holding a `succeeded` Payment with 409, create the `pending` Payment with a generated Idempotency_Key inside one transaction with the gateway call last so a failure leaves no pending row, return the order reference and public key, log only reference/amount/currency/status
    - _Requirements: 16.3, 16.4, 17.1, 17.2, 17.5, 17.6, 17.7, 17.8, 23.2, 23.4_

  - [x] 13.4 Implement `core/views/webhooks.py`
    - CSRF-exempt, unauthenticated endpoint relying solely on signature verification; verify before parsing; `WebhookEvent.get_or_create(event_id)` guard; settle under `transaction.atomic()` with `select_for_update()` on the Payment row; success sets `succeeded` plus Invoice settled, failure sets `failed` leaving the Invoice open, unmatched order returns 200 flagged for reconciliation
    - Strip card-data keys from the stored payload; register the view in `NON_TENANT_VIEWS`
    - _Requirements: 18.1, 18.3, 18.4, 18.5, 18.6, 18.7, 18.9, 18.10, 23.2_

  - [x]* 13.5 Write property test for order creation
    - **Property 22: Order creation is safe and complete**
    - **Validates: Requirements 17.1, 17.2, 17.5, 17.6, 17.8, 16.3, 16.4**

  - [x]* 13.6 Write property test for webhook idempotency
    - **Property 23: Webhook processing is idempotent**
    - **Validates: Requirements 18.6, 18.4, 18.5, 18.7, 18.9, 17.7**

  - [x]* 13.7 Write property test for unverified webhooks
    - **Property 24: Unverified webhooks change nothing**
    - **Validates: Requirements 18.2, 18.3**

  - [x]* 13.8 Write property test for credential and secret containment
    - **Property 26: No credential or secret value ever leaves the Platform**
    - **Validates: Requirements 23.4, 23.2, 23.5**

  - [x]* 13.9 Write unit test for the webhook response budget
    - Assert the handler completes within the 10-second budget for a verified event
    - _Requirements: 18.8_

- [x] 14. Checkpoint - money paths
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Serializers, remaining views, admin, and URL wiring
  - [x] 15.1 Implement `core/serializers.py`
    - Inject `gym` from the request context and pop any client-supplied `gym`; validate `trainer.gym_id` and `plan.gym_id` against the context Gym with field-named errors; expose no manually settable active or status field on MemberProfile while accepting a supplied `join_date`; expose no card-data field
    - _Requirements: 2.3, 2.6, 2.7, 11.6, 15.7, 20.4, 20.9, 23.3_

  - [x] 15.2 Implement `core/views/profiles.py`
    - `GET/PATCH /api/me` returning the computed active state and latest Membership end date; owner invite and list endpoints for Trainers and Members routed through the seat service; trainer-permitted MemberProfile creation
    - _Requirements: 11.4, 15.10, 20.10, 24.3, 5.1, 5.2, 5.7_

  - [x] 15.3 Implement `core/views/catalogue.py`
    - Non-tenant `GET /api/saas-plans` and tenant-scoped `GET /api/membership-plans`
    - _Requirements: 15.9, 24.3_

  - [x] 15.4 Implement `core/views/billing.py`
    - Invoice list and detail scoped to the payer, and the receipt endpoint available to the payer once a Payment reaches `succeeded`
    - _Requirements: 15.8, 19.9, 24.4_

  - [x] 15.5 Wire the URLconf
    - `core/urls.py` exposing exactly the Phase 1 endpoint set and nothing for workout tracking, body metrics, form checks, diet plans, attendance, equipment, or notifications; include it from `gymapp/urls.py` under `/api/`
    - _Requirements: 24.2, 24.3, 24.4, 24.5_

  - [x] 15.6 Implement audited admin registration
    - `core/admin.py` base `ModelAdmin` writing an AuditRecord for every write on a tenant-scoped model, naming actor, record id, record Gym, operation, and timestamp; register AuditRecord read-only
    - _Requirements: 3.10, 22.2_

  - [x]* 15.7 Write property test for tenant isolation
    - **Property 1: Tenant isolation**
    - **Validates: Requirements 3.1, 3.4, 2.2, 15.9**

  - [x]* 15.8 Write property test for existence non-disclosure
    - **Property 2: Cross-Gym record existence is not disclosed**
    - **Validates: Requirements 3.5, 3.2, 3.3**

  - [x]* 15.9 Write property test for tenant assignment
    - **Property 4: Tenant assignment ignores client input**
    - **Validates: Requirements 2.3**

  - [x]* 15.10 Write property test for cross-Gym references
    - **Property 5: Cross-Gym references are rejected by field**
    - **Validates: Requirements 2.6, 2.7**

  - [x]* 15.11 Write property test for per-Gym uniqueness
    - **Property 6: Uniqueness is scoped per Gym**
    - **Validates: Requirements 2.4, 2.5**

  - [x]* 15.12 Write property test for role monotonicity
    - **Property 14: Effective permissions never exceed the stored role**
    - **Validates: Requirements 15.7, 11.6, 15.2, 15.4, 15.8, 15.11, 13.8, 3.1**

  - [x]* 15.13 Write property test for trainer scope
    - **Property 15: Trainer scope follows the assignment graph**
    - **Validates: Requirements 15.3, 15.10**

  - [x]* 15.14 Write property test for the write gates
    - **Property 35: Subscription state gates writes but never reads**
    - **Validates: Requirements 21.5, 20.8**

- [x] 16. Conformance commands and deployment-gate checks
  - [x] 16.1 Implement `check_tenant_scoping`
    - `core/management/commands/check_tenant_scoping.py` walking the resolved URLconf, classifying every view as tenant-scoped unless registered in `NON_TENANT_VIEWS`, and exiting non-zero when a tenant-scoped view lacks `TenantScopedQuerysetMixin` in its MRO
    - _Requirements: 3.7, 3.8, 3.9_

  - [x] 16.2 Implement `check_api_surface`
    - `core/management/commands/check_api_surface.py` failing when any URL pattern name or path segment matches the deferred categories
    - _Requirements: 24.5, 24.6_

  - [x]* 16.3 Write property test for the Phase 1 API surface
    - **Property 38: Phase 1 API surface excludes deferred categories**
    - **Validates: Requirements 24.5, 3.7, 3.8**

  - [x]* 16.4 Write unit tests for both conformance commands
    - Register a deliberately non-conforming view and a deferred-category route, and assert each command fails
    - _Requirements: 3.9, 24.6_

  - [x]* 16.5 Write the single-execution deployment-gate check module
    - Assert `manage.py check --deploy` reports no issue at WARNING or above, `makemigrations --check --dry-run` reports nothing pending, `migrate` succeeds on an empty database, exactly one migration file exists with non-nullable Gym FKs and email identity, no card-data field name appears on any model or serializer, the Phase 1 routes are present, and the webhook view is CSRF-exempt with no authentication class
    - _Requirements: 7.4, 9.1, 9.3, 9.4, 9.5, 9.6, 18.1, 18.10, 23.1, 23.3, 24.2, 24.3, 24.4_

  - [x]* 16.6 Write the gateway integration tests
    - Marked `@pytest.mark.integration` and excluded from the default run: create a real sandbox order, replay a captured signed webhook and assert settlement, and drive a sandbox failure event to a `failed` Payment with the Invoice still open
    - _Requirements: 17.1, 18.4, 18.5_

- [x] 17. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP; the core implementation tasks are not optional.
- Task 5 is destructive by design: it deletes `db.sqlite3` and the existing `core/migrations/0001_initial.py`. There is no production data to preserve, per the fixed decision in the requirements, but the deletion is irreversible for any local data currently in that database.
- Property tests are placed immediately after the code they validate so failures surface early. Each of the 40 design properties has exactly one test module.
- Properties 16, 25, 30, 31 are stateful (`RuleBasedStateMachine` with `@invariant()`); Properties 20, 32 need a PostgreSQL test database because SQLite locking is too coarse to prove the concurrency criteria.
- Configuration, hardening, and surface criteria are covered by the single-execution checks in task 16.5 rather than by property tests, since their behaviour does not vary with input.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1", "2.3", "2.4", "3.1", "3.2", "3.3"] },
    { "id": 2, "tasks": ["2.2", "3.4", "3.5", "4.1"] },
    { "id": 3, "tasks": ["2.5", "2.6", "4.2"] },
    { "id": 4, "tasks": ["4.3"] },
    { "id": 5, "tasks": ["4.4"] },
    { "id": 6, "tasks": ["4.5"] },
    { "id": 7, "tasks": ["4.6"] },
    { "id": 8, "tasks": ["4.7", "5.1"] },
    { "id": 9, "tasks": ["5.2"] },
    { "id": 10, "tasks": ["5.3", "7.1", "7.2", "7.3", "7.5"] },
    { "id": 11, "tasks": ["7.4"] },
    { "id": 12, "tasks": ["7.6", "7.7", "7.8", "7.9", "8.1", "8.2", "8.3"] },
    { "id": 13, "tasks": ["8.4"] },
    { "id": 14, "tasks": ["8.5"] },
    { "id": 15, "tasks": ["8.6", "8.7", "8.8", "8.9", "8.10", "8.11", "8.12", "8.13", "8.14", "10.1", "10.2"] },
    { "id": 16, "tasks": ["10.3", "10.4", "10.5", "10.6", "10.7", "10.8", "10.9", "10.10", "11.1", "12.1", "13.1"] },
    { "id": 17, "tasks": ["11.2", "11.3", "11.4", "11.5", "12.2", "13.2"] },
    { "id": 18, "tasks": ["11.6", "13.3", "15.1"] },
    { "id": 19, "tasks": ["13.4", "15.2", "15.3", "15.4", "15.6"] },
    { "id": 20, "tasks": ["15.5"] },
    { "id": 21, "tasks": ["13.5", "13.6", "13.7", "13.8", "13.9", "15.7", "15.8", "15.9", "15.10", "15.11", "15.12", "15.13", "15.14", "16.1", "16.2"] },
    { "id": 22, "tasks": ["16.3", "16.4", "16.5", "16.6"] }
  ]
}
```
