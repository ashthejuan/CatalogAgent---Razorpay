# CatalogAgent — Security Model

Companion to `CatalogAgent-PRD.md`. This is the security story you present to judges: what is authenticated, what is authorized, what is bounded, and what is honestly out of scope.

---

## 1. Core principle: two different questions, two different layers

| Question | Layer | Nature |
|---|---|---|
| *Who may talk to us?* | Auth (API keys) | Identity-level, per-request |
| *What action may ever be agreed?* | Policy engine | Action-level, per-proposal |

Conflating these is the classic mistake. An authenticated buyer can still propose illegal terms; the guardrail exists precisely because identity ≠ permission-to-transact-at-any-terms.

**Who is a client here:** only buyer agents (external HTTP callers) and judges/humans hitting GETs. **The seller is not a client** — the seller IS the server. Seller authority derives from owning the database, floors, and audit log; it authenticates to no one.

## 2. Gate 1 — Identity & authorization (transport)

Static scoped API keys, Razorpay-test-mode style:

- Header: `X-Buyer-Key: bk_...`
- `buyers` table stores **SHA-256 hashes only** (indexed). Plaintext keys are shown once at provisioning (`python -m app.create_buyer acme --budget 50000`), stored by the operator in `.env`, never committed.
- Lookup by hash index; comparison via `hmac.compare_digest()` (constant-time).
- Optional HMAC pepper (server-side secret from `.env`) hardens low-entropy keys against offline brute-force of a stolen DB.

Endpoint requirements:

| Endpoint | Requirement | Rationale |
|---|---|---|
| `GET /catalog` | **none — deliberately public** | The premise: ANY AI agent may discover the merchant |
| `POST /quote` | valid buyer key | Opens owned negotiations |
| `POST /negotiate` | valid key + ownership | Buyer A cannot advance B's negotiation |
| `GET /orders/{id}`, `/invoices/{id}`, `/audit/{id}` | valid key + ownership | Buyer A cannot read B's contract — the only genuinely sensitive read |

Ownership check = one comparison: `negotiation.buyer_id == buyer.buyer_id`.

Unknown/expired key → bare **401**, no detail (don't educate attackers). Ownership violation → **403**.

### Why hashed, why not encrypted
Keys are verify-only secrets: they are never needed again after issuance, so a one-way hash is correct. Encryption would imply decryption capability we neither want nor need. Threat coverage is honest: this protects *at-rest* reads of the DB file; raw keys also live in `.env`, so real hygiene prioritizes (a) secrets never in git, (b) TLS in transit (ngrok/HTTPS), (c) hashed at rest — in that order.

## 3. Gate 2 — Schema validation (input integrity)

LLM tool output is untrusted input. Every proposal passes through `CounterOffer.model_validate()` before touching money logic:

- Malformed JSON, missing fields, wrong types, hallucinated `product_id` → rejected cleanly as **422-equivalent**, audited as `guardrail_check / FAIL / malformed_proposal`.
- Nothing freeform ever reaches the order pipeline. Structured tool calls only.

## 4. Gate 3 — Action bounding (the actual moat)

Even valid proposals from valid buyers cannot produce out-of-bounds deals:

1. **Per-field bounds**: unit_price ≥ tier floor; payment_terms_days ≤ max; delivery_days ≥ lead-time minimum; qty ≤ stock; turns < max_turns.
2. **Composite margin check**: effective margin after payment-terms working-capital cost and rush-delivery cost. A price legal alone can fail as a package (deep discount + net-45).
3. Verdicts are deterministic Python: `Verdict(PASS|FAIL, reason)` — reasons are machine-generated strings, never LLM text.

**Money-action gating:** the Razorpay test-mode Orders API call (`payments.create_order`) is the only external money action in the system, and it is reachable ONLY from the post-PASS code path. There is no route, tool, or LLM-accessible function that can invoke it before Gate 3 clears — the guardrail isn't a convention, it's a control-flow property.

On FAIL: graceful fallback (`best_legal_counter(session)`) or `escalate_to_human(reason)` — blocking ≠ crashing. This is the "failure handled gracefully" bar item, demonstrated live in the demo video.

Note the asymmetry: transport failures get silence (401); *legitimate* policy failures get explanations (a well-behaved buyer agent should be able to recover and re-negotiate).

## 5. What is honestly NOT solved

- **Agent verification**: possession of a key proves possession, not who operates the agent or which model drives it. Cryptographic proof of agent intent is an open industry problem — that's what AP2's signed mandates address. Our README states this plainly rather than pretending otherwise.
- **Dynamic credentials**: token issuance (`/token`, short-lived signed JWTs) is strictly better at scale but adds zero security with three synthetic buyers — the bootstrap endpoint itself becomes the perimeter. Documented in the production path; deliberately not built. Proportionality is the security decision.
- **Rate limiting / abuse**: irrelevant at demo scale; production maps to per-key rate limits at API Gateway.
- **Audit tamper-proofing**: our log is append-only by application discipline; production adds cryptographic immutability (S3 Object Lock / QLDB).

## 6. Credential evolution path

```
Stage 1 (built):  static scoped API keys, hashed at rest, CLI-provisioned
Stage 2 (doc'd):  client-credentials exchange → short-lived signed tokens
                  carrying identity + scopes (standard OAuth2 CC flow)
Stage 3 (doc'd):  signed agent mandates (AP2-style): cryptographic proof
                  of who authorized an agent to spend, on what terms
```

Each stage is proportionate to its tenant count. Being able to name all three — and explain why stage 1 suffices here — is the senior answer to "why didn't you implement tokens?"

## 7. Security checklist for build

- [ ] `.gitignore` covers `.env`; grep repo for any raw `bk_` key before pushing
- [ ] `buyers.buyer_key_hash` unique + indexed; no plaintext column exists anywhere
- [ ] `hmac.compare_digest` used for key comparison
- [ ] 401 vs 403 vs 422 paths tested (`test_policy.py` + route tests)
- [ ] Malformed-tool-call case covered: rejected, audited, graceful response asserted
- [ ] Composite-margin FAIL case covered end-to-end (block → fallback → audit rows)
- [ ] README carries the two-sentence summary (below)

**Pitch summary (memorize):**
> *"Parties authenticate with scoped API keys — the same model as Razorpay's own test-mode credentials, hashed at rest and compared in constant time. But identity is the small half: every proposed transaction independently passes schema validation and a deterministic bounds engine before anything touches an order. Identity says who may negotiate; the policy engine decides what may ever be agreed."*
