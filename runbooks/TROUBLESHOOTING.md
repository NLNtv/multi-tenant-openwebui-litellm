# Operational Runbook: Troubleshooting & Diagnostics

## Purpose
This runbook provides rapid diagnostic procedures for triaging issues across tenant OpenWebUI instances, the dedicated per-tenant LiteLLM Proxy, enterprise LDAPS authentication, and zero-trust NetworkPolicies.

---

## 1. Quick Diagnostic Decision Tree

```
Issue Observed
│
├── Ingress / HTTP 502 / 504 Bad Gateway
│   └── Go to Section 2: Pod Health & Ingress Routing
│
├── Users Cannot Log In (LDAPS Failure)
│   └── Go to Section 3: Directory & LDAPS Authentication
│
├── Chat Generation Fails / Immediate Error Toast
│   └── Go to Section 4: Dedicated LiteLLM Proxy & Budget Limits
│
└── Network Connection Dropped / Timeout
    └── Go to Section 5: NetworkPolicy Packet Drops
```

---

## 2. Pod Health & Ingress Routing

### Symptoms:
* User receives HTTP 502 Bad Gateway or HTTP 504 Gateway Timeout on `tenant.ai.saasdomain.com`.
* Ingress controller logs: `upstream connect error or disconnect/reset before headers`.

### Diagnostic Steps:
1. **Check Pod Status in Tenant Namespace**:
   ```bash
   kubectl get pods -n tenant-<tenant-id>
   ```
   * Ensure both `openwebui` and `litellm` pods are `1/1 Running`.
2. **Inspect OpenWebUI Events & Logs**:
   ```bash
   kubectl describe pod -n tenant-<tenant-id> -l app.kubernetes.io/name=openwebui
   kubectl logs -n tenant-<tenant-id> -l app.kubernetes.io/name=openwebui --tail=100
   ```

---

## 3. Directory & LDAPS Authentication Failures

### Symptoms:
* Users submit credentials on OpenWebUI login page, but receive `Invalid username or password`.
* Container logs report: `LDAP authentication error: unable to bind to server` or `CERTIFICATE_VERIFY_FAILED`.

### Diagnostic Steps:
1. **Check LDAPS Server Reachability**:
   ```bash
   kubectl exec -it deployment/openwebui -n tenant-<tenant-id> -- nc -zv <LDAP_SERVER_HOST> 636
   ```
2. **Check CA Certificate Trust**:
   ```bash
   kubectl exec -it deployment/openwebui -n tenant-<tenant-id> -- ls -la /app/backend/certs/
   ```
3. **Verify Bind Credentials**:
   ```bash
   kubectl get secret openwebui-credentials -n tenant-<tenant-id> -o jsonpath='{.data.LDAP_APP_PASSWORD}' | base64 -d
   ```

---

## 4. Dedicated LiteLLM Proxy & Budget Issues

### Symptoms:
* Users submit prompts and receive error banner: `BudgetExceededError` or `429 Too Many Requests`.
* Streaming hangs or fails to begin.

### Diagnostic Steps:
1. **Check Dedicated LiteLLM Logs**:
   ```bash
   kubectl logs -n tenant-<tenant-id> deployment/litellm --tail=100
   ```
   * Look for `BudgetExceededError`, `RateLimitExceeded`, or upstream provider errors (401 invalid API key, 403 quota exceeded).
2. **Verify Upstream BYOK Keys**:
   ```bash
   kubectl get secret openwebui-credentials -n tenant-<tenant-id> -o jsonpath='{.data.OPENAI_API_KEY}' | base64 -d
   ```
   * If using Azure OpenAI or AWS Bedrock, verify the endpoint and IAM role are active.
3. **Check Router & Fallback Rules**:
   Inspect mounted configmap:
   ```bash
   kubectl get configmap openwebui-litellm-config -n tenant-<tenant-id> -o yaml
   ```

---

## 5. NetworkPolicy Packet Drops

### Symptoms:
* OpenWebUI pod cannot reach local LiteLLM proxy on port 4000.
* LiteLLM proxy cannot reach public cloud LLMs on port 443.

### Diagnostic Steps:
1. **Test Intra-Namespace Connectivity (OpenWebUI -> LiteLLM)**:
   ```bash
   kubectl exec -it deployment/openwebui -n tenant-<tenant-id> -- curl -m 3 http://litellm:4000/health
   # Expected: {"status": "healthy"}
   ```
2. **Test Outbound HTTPS from LiteLLM**:
   ```bash
   kubectl exec -it deployment/litellm -n tenant-<tenant-id> -- curl -m 5 https://api.openai.com
   ```
   * If timed out: Verify outbound NAT/Internet gateway on cluster node.
3. **Confirm Cross-Tenant Traffic is Blocked (Security Check)**:
   ```bash
   kubectl exec -it deployment/openwebui -n tenant-<tenant-id> -- curl -m 3 http://litellm.tenant-other:4000
   # Must timeout / be dropped by NetworkPolicy!
   ```
