# Operational Runbook: Troubleshooting & Diagnostics

## Purpose
This runbook provides rapid diagnostic procedures for triaging common operational issues across tenant OpenWebUI instances, the LiteLLM Gateway, enterprise LDAPS authentication, and zero-trust NetworkPolicies.

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
│   └── Go to Section 4: LiteLLM Gateway & Budget Limits
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
2. **Inspect Container Events**:
   ```bash
   kubectl describe pod -n tenant-<tenant-id> -l app.kubernetes.io/name=openwebui
   ```
   * Look for `CrashLoopBackOff`, `OOMKilled`, or failed liveness probes (`/health`).
3. **Inspect OpenWebUI Logs**:
   ```bash
   kubectl logs -n tenant-<tenant-id> -l app.kubernetes.io/name=openwebui --tail=100
   ```
4. **Common Root Causes**:
   * **OOMKilled**: Memory limit reached during document vectorization. Solution: Increase `memoryLimit` in `tenant-spec.yaml` and re-apply.
   * **Database lock**: SQLite locked due to concurrent file operations. Solution: Restart pod.

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
   * If timeout occurs: NetworkPolicy is blocking egress or corporate firewall is dropping port 636.
2. **Check CA Certificate Trust**:
   * If using internal enterprise PKI, verify custom CA is mounted:
     ```bash
     kubectl exec -it deployment/openwebui -n tenant-<tenant-id> -- ls -la /app/backend/certs/
     ```
   * If self-signed or private CA is rejected, verify `LDAP_CA_CERT_FILE` points to valid PEM certificate in secret.
3. **Verify Bind Credentials**:
   Extract and test service account credentials directly:
   ```bash
   kubectl get secret openwebui-credentials -n tenant-<tenant-id> -o jsonpath='{.data.LDAP_APP_PASSWORD}' | base64 -d
   ```
4. **Check User Search Filter & Base DN**:
   * Ensure `LDAP_SEARCH_BASE` matches the actual user OU in Active Directory.
   * Ensure `LDAP_ATTRIBUTE_FOR_USERNAME` is `sAMAccountName` (for Windows AD) or `uid` (for OpenLDAP).

---

## 4. LiteLLM Gateway & Budget Issues

### Symptoms:
* Users submit prompts and receive error banner: `BudgetExceededError` or `429 Too Many Requests`.
* Streaming hangs or fails to begin.

### Diagnostic Steps:
1. **Check Tenant Spend & Budget**:
   ```bash
   python -m tenant_manager.cli status <tenant-id>
   ```
   * If `Live Spend >= Max Budget`: The hard budget cutoff has triggered as designed!
   * Solution: If the tenant authorized a budget increase, run:
     ```bash
     python -m tenant_manager.cli update-budget <tenant-id> --budget <new_amount>
     ```
2. **Check Gateway Pod Status**:
   ```bash
   kubectl get pods -n litellm
   kubectl logs -n litellm -l app.kubernetes.io/name=litellm-proxy --tail=100
   ```
3. **Verify Upstream Provider Status**:
   * Check if upstream provider (OpenAI / Anthropic / AWS Bedrock) is experiencing an outage.
   * Verify LiteLLM router fallbacks are kicking in (check `litellm` logs for `Switching to fallback model`).

---

## 5. NetworkPolicy Packet Drops

### Symptoms:
* Tenant pod cannot resolve internal DNS, cannot reach LiteLLM Gateway, or cannot reach LDAPS server.

### Diagnostic Steps:
1. **Verify Ingress-Nginx Labeling**:
   The tenant NetworkPolicy whitelists `ingress-nginx` namespace by label:
   ```bash
   kubectl get namespace ingress-nginx --show-labels
   # Must include: kubernetes.io/metadata.name=ingress-nginx
   ```
2. **Verify LiteLLM Namespace Labeling**:
   The tenant NetworkPolicy whitelists egress to `litellm` namespace by label:
   ```bash
   kubectl get namespace litellm --show-labels
   # Must include: kubernetes.io/metadata.name=litellm
   ```
3. **Verify Egress CIDR for LDAPS**:
   In `charts/openwebui-tenant/templates/networkpolicy.yaml`, verify that the corporate LDAPS server IP is not within a blocked range (`10.0.0.0/8`, `172.16.0.0/12`, etc.) without explicit CIDR inclusion. If the corporate directory is on a private intranet IP, adjust `networkPolicy.ldapServerCidr` in values.yaml.
