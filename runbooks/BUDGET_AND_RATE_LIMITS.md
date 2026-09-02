# Operational Runbook: Managing Budgets, Quotas, & Rate Limits (Per-Tenant Proxy)

## Purpose
This runbook explains how the dedicated per-tenant LiteLLM Proxy enforces localized governance, budget cutoffs, rate limits, and model whitelisting.

---

## 1. Localized Governance Topology

```
                     ┌──────────────────────────────────────────────┐
                     │ Tenant User Request in OpenWebUI             │
                     └──────────────────────┬───────────────────────┘
                                            │ Local HTTP (Port 4000)
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │ Dedicated LiteLLM Proxy (tenant-<id>:4000)   │
                     └──────┬────────────────────────────────┬──────┘
                            │                                │
                 Check RPM/TPM Rate Limits         Check Monthly Spend
                            │                                │
                            ▼                                ▼
                 ┌────────────────────┐            ┌────────────────────┐
                 │ Local Sliding-     │            │ Local Spend DB     │
                 │ Window Counter     │            │ (SQLite / Memory)  │
                 └──────────┬─────────┘            └─────────┬──────────┘
                            │                                │
                   Exceeded? (HTTP 429)             Spend >= max_budget?
                            │                       (HTTP 400/429 Cutoff)
                            ▼                                │
                 [ Immediate Rejection ]                     ▼
                 (Upstream NEVER contacted)       [ Immediate Rejection ]
```

---

## 2. Hard Budget Cutoffs

* **Hard Budget Enforcement (`max_budget`)**:
  When a tenant's cumulative spend meets or exceeds `max_budget`, the local LiteLLM proxy immediately rejects all subsequent `/v1/chat/completions` or `/v1/embeddings` requests with:
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
  Upstream provider APIs (OpenAI, Anthropic, Bedrock, Azure) are never called once cutoff triggers. Costs are capped strictly at the contracted limit.

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
#   Status:      active
```

---

## 4. Dynamically Adjusting Tenant Budgets

If a client upgrades their subscription tier or requests an emergency budget expansion:

1. Update the tenant specification in `tenants/active/<tenant-id>.yaml`:
   ```yaml
   governance:
     maxBudgetUsd: 1000.00
     rpmLimit: 250
     tpmLimit: 300000
   ```
2. Re-apply the updated manifests:
   ```bash
   python -m tenant_manager.cli provision tenants/active/<tenant-id>.yaml
   ```
   Kubernetes performs a rolling update of the `openwebui-litellm-config` ConfigMap without dropping active client connections.

---

## 5. Rate Limiting (RPM / TPM)

LiteLLM tracks:
* **RPM (Requests per Minute)**: Number of inference calls per minute.
* **TPM (Tokens per Minute)**: Sum of prompt tokens and completion tokens generated per minute.

If a tenant experiences an anomalous surge, requests exceeding the limit receive HTTP 429 `RateLimitExceeded`.

---

## 6. Model Whitelisting & Tiering

Tenants can only query models explicitly configured in their local `config.yaml`. If a tenant user queries a model outside their whitelist, LiteLLM returns:
```json
{
  "error": {
    "message": "Model 'gpt-4o-128k' is not permitted under tenant key permissions.",
    "type": "permission_denied"
  }
}
```
