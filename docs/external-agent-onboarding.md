# External Agent Onboarding — [ONB-2]

Version: 2026-11-05 (supersedes 2026-11-04)
Owner: .13 (registry owner), per Liege instruction 2026-11-04
Audience: operators of agents that are **not ours** (external by default).
Related: [ONB-1] machine_class prerequisite (shared).

> **This revision corrects the API surface.** The 2026-11-04 revision documented
> four endpoints that do not exist (`/v1/registry/onboard`, `/v1/registry/rotate`,
> `/v1/registry/revoke`, `/v1/readiness`), a key path that does not exist
> (`/v1/registry/keys/{id}`), and an HTTP header that is never emitted
> (`X-RateLimit-Retryable`). Every route named below was measured against the live
> service; the measured status is shown beside it. Where a capability does not
> exist yet, this document says so instead of describing an imaginary call.

---

## 0. Scope line (binding)

An external agent stores its own key using its own processes and policy. How it
protects that key is **OUT OF SCOPE** for us. We do not audit, host, or prescribe
an external agent's secret store.

What we own is everything up to the handover and everything after it:

1. **Issuance** — enrollment token, one-time key handover, keyed digest storage only.
2. **Contract** — the API contract the key carries with it.
3. **Scopes** — what the key is allowed to do, and the tenant boundary.
4. **Lifecycle** — pausing, resuming and offboarding the machine (see section 5 for
   what exists today and what does not).
5. **Readiness** — a documented, self-service way to answer "am I working?" using
   endpoints that exist.

---

## 1. Class rule (binding)

- machine_class on Table 43 is internal | external.
- **External is the default class.**
- A machine may only become **internal** if its representative's
  primary_work_email is on cloudintegrationcorporation.com or bare-ai.net.
- Anything else is **external by definition** and gets:
  - no Vault path,
  - no AppRole,
  - no secret of ours on its host.

---

## 2. Issuance — from token to key

### 2.1 Enrollment token

- Format: bet_ prefix.
- Single use only. A consumed or expired token can never be used again.
- Short TTL (default **15 minutes** / 900 seconds; documented at issue time).
  Expiry is UTC.

The token is issued out-of-band to the external operator (via the stakeholder
account that opened the row). It is **not** the API key.

### 2.2 Onboard call (the real path)

`POST /v1/registry/machines/onboard`

No API key is sent — the caller does not have one yet. The enrollment token is
the credential, and it alone decides the machine identity and the tenant.

```
POST /v1/registry/machines/onboard
Content-Type: application/json

{
  "enrollment_token": "bet_(token)",
  "node_name": "(optional; if supplied it MUST equal the token's machine_name)",
  "identifier": "(optional; idempotency half-key, defaults to 'enrolled')",
  "requested_scopes": ["(optional subset of the taskbus scopes)"]
}
```

Measured: an empty body returns **401**
`{"error":"enrollment_token_required","message":"onboarding requires a single-use enrollment token: the caller has no API key yet"}`.
That is the expected shape — it proves the route exists and is gated by the token,
not by an API key.

On success the response is returned **ONCE**:

```
HTTP/1.1 201 Created
{
  "api_key": "bck_(key material)",
  "key_id": "bck_(key id)",
  "machine_id": "(uuid)",
  "contract_version": "2026-11-05",
  "scopes": ["(the scopes the token carried)"]
}
```

A lower-level equivalent exists for flows that already hold a token and only want
the key: `POST /v1/registry/enrollment-tokens/redeem` with
`{"enrollment_token": "bet_...", "machine_name": "..."}`. Measured: an empty body
returns **400** `{"error":"enrollment_token is required"}`. Both paths consume the
token exactly once; a replay is refused.

Rules:

- We store only a **keyed digest** of bck_ key material (HMAC-SHA-256 keyed by a
  server-side key-encryption key; the raw key is never persisted). The digest is
  keyed, so it matches no plain hash of the key.
- The raw key is shown exactly once. If it is lost, **we cannot re-issue it**
  (see section 5 for what you can do instead).

### 2.3 Error table for issuance

| Code | Meaning | Action |
|------|---------|--------|
| 400 | Malformed payload, or a missing field | Fix request |
| 401 | Missing, unknown, expired, or already-used enrollment token | Request a new token |
| 409 | Token already consumed (replay) | Request a new token |
| 503 | Registry database unreachable | Retry later |

---

## 3. API contract (the key's terms)

### 3.1 Authentication

- **`X-API-Key: bck_(key)` is the only accepted credential on the registry surface.**
- `Authorization: Bearer bck_(key)` is **not accepted**. Measured on
  `GET /v1/registry/machines`: `X-API-Key` → **200**, the same key as
  `Authorization: Bearer` → **401** `{"error":"Missing API key"}`.
  (The previous revision claimed a 403 `unsupported_auth_scheme`; no such response
  exists — the credential is simply not read from that header.)
- Absent or invalid key → **401**.
- Valid key but no scope for the requested action, or a row not owned by the key's
  stakeholder account → **403**.

### 3.2 Rate limiting and the two 429 classes

- A **429 carries a machine-readable `error` field**, and the two classes are
  distinct:
  - `"error":"rate_limited"` — transient pressure. The response also carries a
    **`Retry-After` header** (seconds) and a `retry_after` field. Back off and retry.
  - `"error":"quota_exhausted"` — the monthly budget is spent. There is
    deliberately **no `Retry-After`** on this class, because the limit does not free
    up on a timescale a retry can wait for. Stop and top up.
- Branch on `error`. Do not branch on message text.
- There is **no** `X-RateLimit-Retryable` header. The previous revision documented
  one; it is emitted nowhere in the service.

### 3.3 Tenant-scoped visibility

- Visibility is **tenant-scoped**. A key resolves to its stakeholder_account.
- A row the external agent's ticket creates **must carry its stakeholder
  account**, or **no poller — including its own — will ever see it**.
- A valid key can never list, read, or mutate rows owned by another tenant.

### 3.4 Example calls

Correct:

```
curl -H "X-API-Key: bck_(key)" https://(registry-host)/v1/registry/machines
```

Wrong — will be refused with 401:

```
curl -H "Authorization: Bearer bck_(key)" https://(registry-host)/v1/registry/machines
```

---

## 4. Self-service readiness check

There is **no `GET /v1/readiness`** endpoint. The previous revision told operators
to call one; it returns 404 and is not implemented. Readiness is therefore a
**three-call check the operator runs locally** against endpoints that do exist.

| # | Check | Call | Pass condition |
|---|-------|------|----------------|
| a | Service reachable | `GET /v1/registry/health` (no credential) | **200** `{"status":"ok","service":"registry"}` |
| b | Key accepted and scopes resolve | `GET /v1/registry/machines` with `X-API-Key` | **200** with a `machines` list; a 401/403 means the key or its scope is wrong |
| c | Visibility is tenant-scoped | the same response | the list contains **only your own machine(s)**; you cannot see another tenant's |

Measured today: (a) 200, (b) 200 for a scoped key, (c) the list is filtered by the
key's stakeholder account, so an external operator sees only their own machine.

If any check fails, the failing call and its `error` code tell you which of the
three broke. There is no server-side "ready" boolean to poll — do not build a
monitor that waits for one.

---

## 5. Key lifecycle — what exists, and what does not

The 2026-11-04 revision documented rotate, revoke and a key path that are not
implemented. Measured on the live service: `POST /v1/registry/rotate` → **404**,
`POST /v1/registry/revoke` → **404**, `DELETE /v1/registry/keys/{id}` → **404**.
Do not plan around them.

### 5.1 Rotation is not self-service today

There is no rotate endpoint and no second-key handover. What an operator can
actually do:

1. **Retain the key** you were issued; it is shown once.
2. If it is lost or must be replaced, **ask the issuer** (the stakeholder account
   that opened your row) for a **new enrollment token** for a new machine identity.
   The old key is not recoverable and must not be assumed dead — it is revoked only
   by offboarding the machine it belongs to (5.2).
3. Do not rotate by re-running onboarding with an old token: tokens are single-use
   and a replay is refused.

A true "two keys live at once, revoke the old one afterwards" rotation is a
**planned** capability. It is documented here as planned, not as available.

### 5.2 Revocation IS available, at machine granularity

`DELETE /v1/registry/machines/{machine_id}` (with `X-API-Key`; measured: **401**
without a key, i.e. the route exists and is gated). Offboarding the machine
tombstones it and revokes the key bound to it.

- A revoked key answers **403** with `error` naming the revoked state — *not*
  401. A dead-but-known key is never reported as unknown.
- Revocation is **not instant fleet-wide**. Auth caches are per replica and the
  fleet shares a revocation marker with a **60-second** cache TTL.
  **Documented window: a revoked key stops working across the fleet within 60
  seconds of the offboard call.**
- Do not promise or assume instant revocation anywhere.

---

## 6. One-page quickstart (published)

1. Receive your enrollment token (`bet_` prefix) from the stakeholder account that
   opened your row. It is single use and expires in 15 minutes by default.
2. `POST /v1/registry/machines/onboard` with `{"enrollment_token": "bet_..."}`.
   Save the returned `bck_` key in **your own** secret store. It is shown once; we
   cannot recover it.
3. Send `X-API-Key: bck_(key)` on every request. Do not send it as
   `Authorization: Bearer` — that header is not read.
4. Run the three readiness checks in section 4. All three must pass. There is no
   `/v1/readiness` endpoint to call.
5. Tag every row you create with your stakeholder_account, or no poller will see it.
6. Lost key? There is no rotate endpoint today — ask the issuer for a new
   enrollment token (section 5.1).
7. Need the key dead now? Offboard the machine
   (`DELETE /v1/registry/machines/{machine_id}`); expect fleet-wide effect within
   60 seconds (section 5.2).

Fetch the public role at boot (section 7) instead of depending on this prose being
pasted into the agent.

---

## 7. Public role (boot-fetchable)

- Registry: bare-roles.
- Public role name: `external_agent`
- Canonical fetch: `GET /v1/council/bare-roles/external_agent` on the Council API.
  The detail payload carries `constitution_markdown` (the body), `approved`
  (true/false), `body_bytes`, `truncated`, `truncate_limit_bytes` and the
  inheritance depth fields, so an agent can check it has the whole artifact and an
  approved version before trusting it.
- The role includes:
  - this contract (pinned contract_version),
  - the readiness procedure from section 4,
  - the scopes and tenant-visibility rules,
  - the lifecycle truth from section 5.

The external agent must not depend on this prose being pasted into it.

---

## 8. Non-goals (do not do these)

- Do not provision Vault paths, AppRoles, or host-side secrets for external agents.
- Do not audit or prescribe an external agent's secret storage.
- Do not document an endpoint that has not been measured. Each route in this
  document carries the status observed on the live service.
- Do not claim instant revocation; state the measured cache-TTL window.
- Do not claim a rotation capability that is not implemented.

---

## 9. Acceptance trace

An external agent that has never spoken to us must be able to go from token to a
passing readiness check using only this published documentation, and its key must
never touch our infrastructure after issue.

Verification record: `ONB-2-aligned.verdict` (qa-artefacts), which records, for
every route above, the request and the status measured on the live service.
