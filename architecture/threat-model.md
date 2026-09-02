# Threat Model & Security Architecture (Decentralized Per-Tenant Gateway)

## 1. Threat Modeling Methodology

This document establishes the security posture and threat model for the Multi-Tenant OpenWebUI SaaS Platform under the **Decentralized Per-Tenant Gateway** architecture, evaluating threats across the **STRIDE** methodology (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege) and mapping mitigations to Kubernetes defense-in-depth controls.

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
       [ Isolated Tenant Perimeter A ]                             [ Isolated Tenant Perimeter B ]
       ┌───────────────────────────────┐                           ┌───────────────────────────────┐
       │ Namespace: tenant-acme        │                           │ Namespace: tenant-globex      │
       │                               │                           │                               │
       │  ┌─────────────────────────┐  │                           │  ┌─────────────────────────┐  │
       │  │ OpenWebUI Pod           │  │                           │  │ OpenWebUI Pod           │  │
       │  └───────────┬─────────────┘  │                           │  └───────────┬─────────────┘  │
       │              │ Local HTTP     │                           │              │ Local HTTP     │
       │              ▼               │                           │              ▼               │
       │  ┌─────────────────────────┐  │                           │  ┌─────────────────────────┐  │
       │  │ Dedicated LiteLLM Proxy │  │                           │  │ Dedicated LiteLLM Proxy │  │
       │  │ (Acme BYOK Credentials) │  │                           │  │ (Globex Credentials)    │  │
       │  └───────────┬─────────────┘  │                           │  └───────────┬─────────────┘  │
       └──────────────┼────────────────┘                           └──────────────┼────────────────┘
                      │                                                           │
                      ▼                                                           ▼
         [ Outbound HTTPS 443 ]                                      [ Outbound HTTPS 443 ]
          (OpenAI / Anthropic)                                        (Azure OpenAI / Bedrock)
```

---

## 3. STRIDE Threat Matrix & Mitigations

### 3.1 Spoofing (Identity & Authentication)
* **Threat S1: Tenant Impersonation via Subdomain Hijacking**
  * *Attack*: An attacker accesses `victim.ai.saasdomain.com` to manipulate another tenant's session.
  * *Mitigation*: Ingress TLS certificates are strictly provisioned per tenant via cert-manager. OpenWebUI session cookies are scoped strictly to the specific subdomain with `SameSite=Lax`, `Secure=true`, and `HttpOnly=true`.
* **Threat S2: Unauthorized Directory Authentication**
  * *Attack*: Attacker attempts to forge LDAP credentials or bypass directory bind checks.
  * *Mitigation*: OpenWebUI communicates over LDAPS (TCP 636) with certificate validation (`LDAP_USE_TLS=true`). Bind credentials use a read-only directory service account restricted to organizational OU search queries.

### 3.2 Tampering (Data & Manifest Integrity)
* **Threat T1: Cross-Tenant Data Tampering**
  * *Attack*: A compromised container in Tenant A attempts to modify files or database records belonging to Tenant B.
  * *Mitigation*: Complete storage and network isolation. Each tenant has a dedicated PersistentVolumeClaim. Kubernetes volumes are mounted with POSIX permissions isolated to UID 1000.
* **Threat T2: LLM Configuration & Router Tampering**
  * *Attack*: A tenant user modifies proxy settings to access unauthorized models.
  * *Mitigation*: LiteLLM `config.yaml` is mounted read-only into the dedicated proxy container from an immutable Kubernetes ConfigMap managed by platform automation.

### 3.3 Repudiation (Auditability & Traceability)
* **Threat R1: Disputed LLM Spend or Untraceable Token Consumption**
  * *Attack*: A tenant disputes high token consumption or unauthorized model usage.
  * *Mitigation*: The dedicated LiteLLM instance records request timestamps, model invocations, prompt token counts, and completion token counts in its localized persistent spend logs.
* **Threat R2: Administrative Actions Lack Traceability**
  * *Attack*: An engineer provisions, modifies, or deletes a tenant stack without an audit trail.
  * *Mitigation*: The `tenant-manager` CLI logs all actions with structured JSON outputs. Kubernetes audit logging captures all namespace, deployment, and secret modifications.

### 3.4 Information Disclosure (Confidentiality & Credential Compartmentalization)
* **Threat I1: Exposure of Global Master API Keys (Eliminated via Per-Tenant Architecture)**
  * *Analysis*: In a centralized architecture, a breach of the gateway exposes keys for *all* tenants.
  * *Mitigation*: **Zero Shared Control Plane**. Upstream provider credentials (e.g. Acme's private OpenAI key or Azure OpenAI key) are stored strictly within `tenant-acme` Kubernetes Secrets. A breach of Tenant A's pod can never expose Tenant B's credentials.
* **Threat I2: Cross-Tenant Prompt Cache Bleed**
  * *Attack*: Tenant B receives a cached LLM completion containing confidential proprietary data submitted earlier by Tenant A.
  * *Mitigation*: Caches are 100% physically isolated per namespace. Tenant A and Tenant B have completely separate proxy runtimes and cache stores. Cross-tenant cache bleed is architecturally impossible.
* **Threat I3: Cloud Metadata Service Exfiltration (SSRF)**
  * *Attack*: A prompt injection or SSRF exploit in OpenWebUI or LiteLLM attempts to query `http://169.254.169.254/latest/meta-data/` to steal node IAM roles.
  * *Mitigation*: Tenant NetworkPolicies explicitly block all egress to link-local addresses (`169.254.169.254/32`). Furthermore, pods run without hostNetwork access.

### 3.5 Denial of Service (Availability & Quota Exhaustion)
* **Threat D1: Central Gateway Outage (Blast Radius Eliminated)**
  * *Analysis*: If a central proxy crashes, all clients lose service.
  * *Mitigation*: **Independent Failure Domains**. If Tenant A's LiteLLM proxy crashes or runs out of memory, Tenant B's workspace continues operating with zero degradation.
* **Threat D2: Financial DoS (Token Flooding / Budget Depletion)**
  * *Attack*: A compromised user loops high-context prompts to rack up exorbitant bills.
  * *Mitigation*: Local LiteLLM enforces sliding-window rate limits (RPM / TPM) and hard monthly budget ceilings (`max_budget`). When spend reaches the ceiling, requests are immediately cut off.
* **Threat D3: Compute & Memory Starvation (Noisy Neighbor)**
  * *Attack*: A tenant ingests a massive document into the vector store, consuming all cluster CPU/RAM.
  * *Mitigation*: Kubernetes `ResourceQuota` and `LimitRange` per tenant namespace enforce hard caps on CPU and Memory requests/limits.

### 3.6 Elevation of Privilege (Container & Cluster Security)
* **Threat E1: Container Breakout to Kubernetes Node**
  * *Attack*: An exploit in Python/FastAPI allows arbitrary code execution leading to host takeover.
  * *Mitigation*: Pod Security Standards enforced at the `restricted` level across all tenant pods:
    * `runAsNonRoot: true` (UID 1000)
    * `allowPrivilegeEscalation: false`
    * `readOnlyRootFilesystem: true` (with writable `/tmp` and `/app/backend/data` emptyDir/PVC)
    * `capabilities.drop: ["ALL"]`
    * `seccompProfile.type: RuntimeDefault`
* **Threat E2: ServiceAccount Token Abuse**
  * *Attack*: An attacker extracts `/var/run/secrets/kubernetes.io/serviceaccount/token` to query the K8s API.
  * *Mitigation*: `automountServiceAccountToken: false` is configured on all tenant pods.

---

## 4. Defense-in-Depth Summary

```
Layer 1: Edge / Ingress       -> TLS termination, Subdomain routing, WAF rate limiting
Layer 2: Network Isolation    -> Zero-Trust NetworkPolicy (No Inter-Tenant Traffic, No Cloud Metadata)
Layer 3: Pod Security         -> Non-root (UID 1000), Drop ALL caps, No K8s API token
Layer 4: Storage Isolation    -> Dedicated RWO PersistentVolumeClaim per tenant, No shared volumes
Layer 5: Local Gateway        -> Dedicated LiteLLM Proxy, Local Rate Limiting & Hard Budget Enforcement
Layer 6: Credential Boundary  -> BYOK / Tenant-Scoped Provider Keys isolated inside namespace secrets
```
