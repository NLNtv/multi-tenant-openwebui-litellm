# Operational Runbook: Managing Budgets, Quotas, & Rate Limits

## Purpose
This runbook explains how the LiteLLM Central Gateway meters tenant usage, enforces hard financial cutoffs, throttles requests via rate limits, and how SREs can dynamically update budgets and models.

---

## 1. How the Governance Mechanism Works

```
                     ┌──────────────────────────────────────────────┐
                     │ Tenant User Request (Virtual Key: sk-tenant) │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │ LiteLLM Proxy Gateway (litellm.litellm.svc)  │
                     └──────┬───────────────────────────────┬───────┘
                            │                               │
                 Check RPM/TPM Rate Limits         Check Monthly Spend
                            │                               │
                            ▼                               ▼
                 ┌────────────────────┐            ┌────────────────────┐
                 │ Redis Token Bucket │            │ PostgreSQL DB      │
                 └──────────┬─────────┘            └────────┬───────────┘
                            │                               │
                   Exceeded? (HTTP 429)             Spend >= max_budget?
                            │                       (HTTP 400/429 Cutoff)
                            ▼                               │
                 [ Immediate Rejection ]                     ▼
                 (Upstream NEVER contacted)       [ Immediate Rejection ]
```

---

## 2. Hard Budget Cutoffs

* **Hard Budget Enforcement (`max_budget`)**:
  When a tenant's cumulative spend meets or exceeds `max_budget`, LiteLLM immediately rejects all subsequent `/v1/chat/completions` or `/v1/embeddings` requests with:
  ```json
  {
    "error": {
      "message": "BudgetExceededError: Key max budget has been exceeded. Current spend: $500.02. Max budget: $500.00",
      "type": "budget_exceeded_error",
      "code": "budget_exceeded"
    }
  }
  ```
* **Zero Financial Leakage**:
  Upstream provider APIs (OpenAI, Anthropic, Bedrock) are never called once cutoff triggers. Costs are capped strictly at the contracted limit.

---

## 3. Auditing Tenant Spend

To check live spend against allocated budget:
```bash
# Using tenant-manager CLI
python -m tenant_manager.cli status acme-corp

# Output:
# Tenant Status: acme-corp
#   Name:        Acme Corporation
#   FQDN:        https://acme.ai.saasdomain.com
#   Max Budget:  $500.00
#   Live Spend:  $432.18 / $500.00
#   Remaining:   $67.82
#   Status:      active
```

---

## 4. Dynamically Adjusting Tenant Budgets

If a client upgrades their subscription tier or requests an emergency budget expansion:

```bash
# Update budget to $1,000.00 immediately without pod restart:
python -m tenant_manager.cli update-budget acme-corp --budget 1000.00

# Also increase rate limits if needed (e.g. 250 RPM, 300k TPM):
python -m tenant_manager.cli update-budget acme-corp --budget 1000.00 --rpm 250 --tpm 300000
```
This directly updates the LiteLLM PostgreSQL store and the local tenant registry. OpenWebUI resumes request delivery instantaneously without restart.

---

## 5. Rate Limiting (RPM / TPM)

LiteLLM tracks:
* **RPM (Requests per Minute)**: Number of inference calls per minute per virtual key.
* **TPM (Tokens per Minute)**: Sum of prompt tokens and completion tokens generated per minute.

Rate limits are maintained in Redis via sliding window counters. If a tenant experiences an anomalous surge (e.g., an automated script firing rapid requests), requests exceeding the limit receive HTTP 429 `RateLimitExceeded`.

---

## 6. Model Whitelisting & Tiering

Tenants can only query models explicitly assigned to their virtual key. If a tenant submits a request for a model outside their whitelist (e.g. `gpt-4o-128k` when only `gpt-4o-mini` was contracted), LiteLLM returns:
```json
{
  "error": {
    "message": "Model 'gpt-4o-128k' is not permitted under tenant key permissions.",
    "type": "permission_denied"
  }
}
```
To update the whitelisted models for a tenant:
```bash
curl -X POST "http://litellm:4000/key/update" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "sk-tenant-acme-...", "models": ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "gemini-1.5-pro"]}'
```
