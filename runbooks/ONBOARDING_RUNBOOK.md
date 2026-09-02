# Operational Runbook: Tenant Onboarding (Decentralized Per-Tenant Gateway)

## Purpose
This runbook guides Site Reliability Engineers (SREs) and Platform Operators through onboarding a new corporate client onto the multi-tenant OpenWebUI SaaS platform featuring dedicated per-tenant LiteLLM gateways and BYOK support.

---

## 1. Pre-Flight Checklist

Before beginning onboarding, collect the following enterprise tenant specifications:

| Requirement | Description | Example |
| :--- | :--- | :--- |
| **Tenant ID** | Lowercase alphanumeric slug (RFC 1123) | `acme-corp` |
| **Subdomain** | Desired subdomain prefix | `acme` $\to$ `acme.ai.saasdomain.com` |
| **Budget & Quota** | Monthly dollar cap and rate limits | \$500.00 / 30d, 120 RPM, 120k TPM |
| **Model Whitelist** | Models contracted by tenant | `gpt-4o`, `claude-3-5-sonnet` |
| **Upstream Keys (BYOK)** | Corporate OpenAI, Anthropic, or Azure keys | `sk-proj-acme-...` |
| **LDAPS Host & Port** | Corporate directory endpoint | `ldaps.corp.acme.com:636` |
| **LDAP Bind DN & Pass**| Service account for LDAP search queries | `CN=svc-ai,OU=Services,DC=acme,DC=com` |
| **LDAP User Search Base**| Directory OU containing permitted users | `OU=Users,DC=acme,DC=com` |
| **Admin Break-Glass** | IT contact for bootstrap recovery | `breakglass-admin@acme.com` |

---

## 2. Directory Connectivity Pre-Check

From an administrative pod or jumper node with cluster network access:
```bash
# Verify outbound TCP connectivity to tenant LDAPS on port 636
nc -zv ldaps.corp.acme.com 636

# Test LDAPS bind and search using ldapsearch
ldapsearch -x -H ldaps://ldaps.corp.acme.com:636 \
  -D "CN=svc-ai,OU=Services,DC=acme,DC=com" \
  -w "VaultStoredSecret" \
  -b "OU=Users,DC=acme,DC=com" \
  "(sAMAccountName=testuser)"
```

---

## 3. Creating Tenant Specification File

Create `tenants/active/acme-corp.yaml` using the platform template:

```yaml
apiVersion: saas.platform.io/v1alpha1
kind: TenantSpecification
metadata:
  tenantId: "acme-corp"
  tenantName: "Acme Corporation"
  contactEmail: "it-admin@acme.com"
  environment: "production"

routing:
  subdomain: "acme"
  baseDomain: "ai.saasdomain.com"
  tlsClusterIssuer: "letsencrypt-prod"

governance:
  maxBudgetUsd: 500.00
  budgetDuration: "30d"
  rpmLimit: 120
  tpmLimit: 120000
  allowedModels:
    - "gpt-4o"
    - "gpt-4o-mini"
    - "claude-3-5-sonnet"
    - "text-embedding-3-small"

upstreamCredentials:
  openaiApiKey: "sk-proj-acme-corp-private-key-2026"
  anthropicApiKey: "sk-ant-acme-corp-private-key-2026"

branding:
  portalTitle: "Acme Corp AI Workspace"
  defaultModel: "gpt-4o"
  customLogoUrl: "https://acme.com/assets/logo.png"

identity:
  ldapEnabled: true
  serverHost: "ldaps.corp.acme.com"
  serverPort: 636
  useTls: true
  validateCert: true
  searchBase: "OU=Employees,OU=Users,DC=corp,DC=acme,DC=com"
  bindDn: "CN=svc-openwebui,OU=ServiceAccounts,DC=corp,DC=acme,DC=com"
  bindPassword: "AcmeSecureBindPassword2026!"
  usernameAttribute: "sAMAccountName"
  searchFilter: "(&(objectClass=user)(memberOf=CN=AI_Access,OU=Groups,DC=corp,DC=acme,DC=com))"

adminFallback:
  enabled: true
  email: "breakglass-admin@acme.com"
  password: "GenerateInitialRecoveryPassword2026!"

compute:
  replicas: 1
  storageSize: "20Gi"
```

---

## 4. Execution & Validation

### Step 4.1: Validate Configuration Syntax
```bash
python -m tenant_manager.cli validate tenants/active/acme-corp.yaml
```
Expected output:
```
[SUCCESS] Configuration 'tenants/active/acme-corp.yaml' is valid!
  Tenant ID:   acme-corp
  Name:        Acme Corporation
  FQDN:        https://acme.ai.saasdomain.com
  Max Budget:  $500.00 / 30d
```

### Step 4.2: Execute Provisioning with Transaction Manager
```bash
python -m tenant_manager.cli provision tenants/active/acme-corp.yaml
```

The CLI executes atomic provisioning of the entire tenant stack:
1. Creates Kubernetes namespace `tenant-acme-corp`.
2. Applies ResourceQuota, LimitRange, Secret (with BYOK credentials), OpenWebUI Branding ConfigMap, LiteLLM ConfigMap, and Dedicated PVC.
3. Applies Dedicated LiteLLM Deployment (`litellm:4000`) and Service.
4. Applies OpenWebUI Deployment (`openwebui:8080`) and Service.
5. Applies Zero-Trust NetworkPolicies for both OpenWebUI and LiteLLM.
6. Applies Ingress with automated cert-manager TLS.
7. Records tenant status in `tenants/active/registry.json`.

---

## 5. Post-Deployment Verification

1. **Verify Pod Status**:
   ```bash
   kubectl get pods -n tenant-acme-corp
   ```
   Both `openwebui` and `litellm` pods must reach `1/1 Running`.

2. **Verify Ingress & TLS**:
   ```bash
   kubectl get ingress -n tenant-acme-corp
   curl -Iv https://acme.ai.saasdomain.com/health
   ```
   HTTP response must be `200 OK`.

3. **Verify Zero-Trust Isolation**:
   Confirm that the tenant pod cannot ping or query neighboring namespaces:
   ```bash
   kubectl exec -it deployment/openwebui -n tenant-acme-corp -- curl -m 3 http://openwebui.tenant-globex:8080
   # Must timeout with connection dropped by NetworkPolicy!
   ```

---

## 6. Client Handover
Send the client IT administrator:
* Portal URL: `https://acme.ai.saasdomain.com`
* Active Directory Authentication instructions (users log in with standard AD domain username and password).
* Emergency break-glass local credentials (delivered via secure one-time secret link).
