# Threat Model & Security Architecture

## 1. Threat Modeling Methodology

This document establishes the security posture and threat model for the Multi-Tenant OpenWebUI SaaS Platform, evaluating threats across the **STRIDE** methodology (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege) and mapping mitigations to Kubernetes defense-in-depth controls.

---

## 2. Attack Surfaces & Threat Vectors

```
                           [ Threat Vector 1: Public Ingress & WebUI Abuse ]
                                                  │
                                                  ▼
                                      ┌───────────────────────┐
                                      │ Ingress (TLS / WAF)   │
                                      └───────────┬───────────┘
                                                  │
                    ┌─────────────────────────────┴─────────────────────────────┐
                    ▼                                                           ▼
      [ Threat Vector 2: Pod Compromise ]                         [ Threat Vector 3: Credential Theft ]
       - Cross-tenant network pivot                                - Upstream LLM key extraction
       - Host escape / container breakout                          - LDAPS bind password leakage
                    │                                                           │
                    ▼                                                           ▼
      ┌───────────────────────────┐                               ┌───────────────────────────┐
      │ Tenant Pod (OpenWebUI)    │                               │ LiteLLM Proxy Gateway     │
      └─────────────┬─────────────┘                               └─────────────┬─────────────┘
                    │                                                           │
                    ▼                                                           ▼
      [ Threat Vector 4: LLM Abuse ]                              [ Threat Vector 5: Supply Chain & Upstream ]
       - Token & budget exhaustion                                 - Upstream provider outage / MITM
       - Prompt injection into backend                             - Data residency violations
```

---

## 3. STRIDE Threat Matrix & Mitigations

### 3.1 Spoofing (Identity & Authentication)
* **Threat S1: Tenant Impersonation via Subdomain Hijacking**
  * *Attack*: An attacker accesses `victim.ai.saas.com` to manipulate another tenant's session.
  * *Mitigation*: Ingress TLS certificates are strictly provisioned per tenant via cert-manager. OpenWebUI session cookies are scoped strictly to the specific subdomain with `SameSite=Lax`, `Secure=true`, and `HttpOnly=true`.
* **Threat S2: Unauthorized Directory Authentication**
  * *Attack*: Attacker attempts to forge LDAP credentials or bypass directory bind checks.
  * *Mitigation*: OpenWebUI communicates over LDAPS (TCP 636) with certificate validation (`LDAP_USE_TLS=true`). Bind credentials use a read-only directory service account restricted to organizational OU search queries.

### 3.2 Tampering (Data & Manifest Integrity)
* **Threat T1: Cross-Tenant Data Tampering**
  * *Attack*: A compromised container in Tenant A attempts to modify files or database records belonging to Tenant B.
  * *Mitigation*: Complete storage isolation. Each tenant has a dedicated PersistentVolumeClaim. Kubernetes volumes are mounted with POSIX permissions isolated to UID 1000. Under no circumstances are multi-tenant PVCs shared.
* **Threat T2: LiteLLM Configuration Tampering**
  * *Attack*: A tenant modifies proxy settings to grant themselves unlimited tokens or access restricted models (e.g. GPT-4o 128k).
  * *Mitigation*: LiteLLM configuration is housed entirely in the `litellm` namespace. Tenant pods cannot reach the Kubernetes API server and have zero RBAC permissions to read or modify ConfigMaps or Secrets outside their namespace.

### 3.3 Repudiation (Auditability & Traceability)
* **Threat R1: Untraceable LLM Spend or Malicious Prompt Generation**
  * *Attack*: A tenant claims they were overbilled or disputes high token consumption.
  * *Mitigation*: LiteLLM logs every request, model invoked, prompt token count, and completion token count in its PostgreSQL audit table, stamped with the tenant's immutable Virtual Key ID and timestamp.
* **Threat R2: Administrative Actions Lack Traceability**
  * *Attack*: An engineer provisions or deletes a tenant without an audit trail.
  * *Mitigation*: The `tenant-manager` CLI logs all actions with structured JSON outputs. Kubernetes audit logging captures all namespace and secret modifications.

### 3.4 Information Disclosure (Confidentiality & Upstream Secrecy)
* **Threat I1: Exposure of Upstream Provider API Keys (OpenAI, Anthropic, Bedrock)**
  * *Attack*: A malicious user or rogue administrator in a tenant instance dumps environment variables or inspects network traffic to steal root provider API keys.
  * *Mitigation*: **Zero-Knowledge Architecture**. Upstream provider keys are NEVER injected into tenant namespaces. The tenant pod only holds a scoped Virtual Key (`sk-tenant-...`). LiteLLM Proxy is the sole entity possessing upstream keys, and its admin API is restricted from tenant pod egress.
* **Threat I2: Cross-Tenant Network Sniffing**
  * *Attack*: A pod attempts to sniff traffic or query neighboring pods in the cluster.
  * *Mitigation*: Kubernetes `NetworkPolicy` (enforced via Calico/Cilium) enforces default-deny ingress and egress. Inter-namespace network traffic is dropped at the kernel level.
* **Threat I3: Cloud Metadata Service Exfiltration (SSRF)**
  * *Attack*: A prompt injection or SSRF exploit in OpenWebUI attempts to query `http://169.254.169.254/latest/meta-data/` to steal node IAM roles.
  * *Mitigation*: Tenant NetworkPolicies explicitly block all egress to link-local addresses (`169.254.169.254/32`). Furthermore, pods run without hostNetwork access.

### 3.5 Denial of Service (Availability & Quota Exhaustion)
* **Threat D1: Financial DoS (Token Flooding / Budget Depletion)**
  * *Attack*: A compromised user account loops high-context prompts to rack up exorbitant LLM bills.
  * *Mitigation*: Dual-layer governance in LiteLLM:
    1. **Rate Limiting**: Sliding-window RPM (Requests per Minute) and TPM (Tokens per Minute) enforced via Redis.
    2. **Hard Budget Cutoff**: `max_budget` enforced in PostgreSQL. When current spend meets `max_budget`, LiteLLM immediately rejects further requests with HTTP 400/429 without contacting upstream providers.
* **Threat D2: Compute & Memory Starvation (Noisy Neighbor)**
  * *Attack*: A tenant ingests a massive document into the vector store, consuming all cluster CPU/RAM.
  * *Mitigation*: Kubernetes `ResourceQuota` and `LimitRange` per tenant namespace enforce hard caps on CPU and Memory requests/limits.

### 3.6 Elevation of Privilege (Container & Cluster Security)
* **Threat E1: Container Breakout to Kubernetes Node**
  * *Attack*: An exploit in Python/FastAPI allows arbitrary code execution leading to host takeover.
  * *Mitigation*: Pod Security Standards enforced at the `restricted` level:
    * `runAsNonRoot: true` (UID 1000)
    * `allowPrivilegeEscalation: false`
    * `readOnlyRootFilesystem: true` (with writable `/tmp` and `/app/backend/data` emptyDir/PVC)
    * `capabilities.drop: ["ALL"]`
    * `seccompProfile.type: RuntimeDefault`
* **Threat E2: ServiceAccount Token Abuse**
  * *Attack*: An attacker extracts `/var/run/secrets/kubernetes.io/serviceaccount/token` to query the K8s API.
  * *Mitigation*: `automountServiceAccountToken: false` is configured on all tenant OpenWebUI pods.

---

## 4. Defense-in-Depth Summary

```
Layer 1: Edge / Ingress       -> TLS termination, Subdomain routing, WAF rate limiting
Layer 2: Network Layer        -> Calico/Cilium NetworkPolicy (Default Deny, No Inter-Tenant, No Metadata)
Layer 3: Pod Security         -> Non-root (UID 1000), Drop ALL caps, Read-only root, No K8s API token
Layer 4: Storage Isolation    -> Dedicated RWO PersistentVolumeClaim per tenant, No shared volumes
Layer 5: Application Gateway  -> LiteLLM Proxy, Virtual Keys, Hard Budget Enforcement, Rate Limiting
Layer 6: Upstream Providers   -> Provider keys isolated in Gateway control plane, encrypted at rest
```
