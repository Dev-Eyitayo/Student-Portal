# Custom Domains Architecture & Implementation Spec

**Status:** Planned (Post-Pilot)  
**Related Schema:** `CustomDomainStatusResponse` in `app/modules/identity/schemas.py`

---

## 1. Overview

By default, all schools can sign in on the shared platform domain (e.g. `app.olivetrecords.com` or `portal.obhsoyo.com`). When a school purchases its own domain (e.g. `portal.ambassadors.com` or `ambassadors.com`), the platform should:

1. Serve the same Cloudflare Pages frontend build on that domain.
2. Display the school's own name and logo on `/login` before sign-in.
3. Prevent users belonging to other schools from signing in through that school's custom domain.
4. Generate invite, password-reset, and result-verification links using that school's custom domain.

---

## 2. Database & Model Changes

### `schools` Table (`app/modules/identity/models.py`)
Add an optional, uniquely indexed column:

```python
custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

With a partial unique index on lowercased non-null values:

```sql
CREATE UNIQUE INDEX uq_schools_custom_domain_active
ON schools (lower(custom_domain))
WHERE custom_domain IS NOT NULL;
```

**Normalization rules:**
- Strip scheme (`https://`, `http://`), paths, trailing slashes, and port numbers.
- Lowercase and trim whitespace (e.g. `Portal.Ambassadors.com/` → `portal.ambassadors.com`).
- Reject `localhost`, `127.0.0.1`, and the shared platform domain(s).

### Schemas (`app/modules/identity/schemas.py`)
- Add `custom_domain: str | None` to `SchoolResponse` and `SchoolProfileUpdateRequest` (restricted to `SUPER_ADMIN` or verified domain setup).
- Update `CustomDomainStatusResponse` to return:
  - `enabled`: `bool(school.custom_domain)`
  - `status`: `"ACTIVE"` when configured, otherwise `"PLANNED_POST_PILOT"`
  - `domain`: `school.custom_domain`

---

## 3. Domain-Locked Login (`POST /api/v1/identity/login`)

Without domain binding, `POST /api/v1/identity/login` authenticates by `email` alone, meaning a user from School B could log into School B while visiting `ambassadors.com`.

### Login Enforcement Flow
1. Extract the request origin hostname from the `Origin` header (falling back to `Referer` hostname).
2. Ignore `localhost`, `127.0.0.1`, and configured shared platform domains (`APP_FRONTEND_DOMAINS`).
3. Look up `SELECT id, name FROM schools WHERE lower(custom_domain) = :host AND is_active = true`.
4. If a school is matched (`domain_school`) and `user.school_id != domain_school.id`:
   - Reject with `403 Forbidden`:
     > `"This portal belongs to {domain_school.name}. Please sign in at your school's portal."`
5. If no `custom_domain` matches (shared platform domain or local dev), allow login for any active school as usual.

> **Security note:** Domain binding is an **additional** UX/access boundary. Tenant isolation continues to be enforced by RS256 JWT `school_id` claims and PostgreSQL Row-Level Security (`SET LOCAL app.current_school_id`).

---

## 4. Pre-Login Branding Endpoint

Add an unauthenticated read-only endpoint:

```http
GET /api/v1/identity/schools/by-domain?domain=portal.ambassadors.com
```

**Response (`200 OK`):**
- When matched to an active school:
  ```json
  {
    "success": true,
    "data": {
      "school_id": "...",
      "name": "Ambassadors College",
      "official_code": "AMB-OYO",
      "school_type": "SECONDARY",
      "logo_url": "https://files.yourdomain.com/..."
    }
  }
  ```
- When not matched (shared domain): `{"success": true, "data": null}`

**Frontend (`/login`):**
- On mount, query `by-domain` with `window.location.hostname`.
- If `data` is non-null, render the school's logo and name on the login card instead of generic platform branding.

---

## 5. Dynamic Email & Result Links

Add a helper in `app/modules/identity/services.py`:

```python
def school_frontend_base_url(school: School | None, fallback_url: str) -> str:
    if school and school.custom_domain:
        return f"https://{school.custom_domain}"
    return fallback_url.rstrip("/")
```

Use `school_frontend_base_url(...)` when generating:
- Staff invitation links (`/accept-invite?token=...`)
- Password reset links (`/reset-password?token=...`)
- Parent/student result verification links (`/results/verify?token=...`)

---

## 6. Dynamic CORS & Cloudflare Pages Setup

### Backend CORS (`app/main.py`)
- **Initial approach:** Add each school's custom domain (`https://portal.ambassadors.com`) to `CORS_ORIGINS` in `.env.prod`.
- **Zero-restart approach:** Cache active `schools.custom_domain` values in Redis/memory (60s TTL) and validate `Origin` dynamically alongside `settings.CORS_ORIGINS`.

### Cloudflare Pages
1. In **Cloudflare Pages → Custom domains**, add `portal.ambassadors.com`.
2. Have the school create a DNS `CNAME` record pointing `portal` → `<your-project>.pages.dev`.
3. Cloudflare automatically provisions SSL/TLS for the custom domain (up to 100 custom domains per Pages project; use **Cloudflare for SaaS** if scaling beyond 100 schools).
