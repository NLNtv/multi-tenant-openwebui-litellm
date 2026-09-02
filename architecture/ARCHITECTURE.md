# System Architecture: Multi-Tenant OpenWebUI SaaS on Kubernetes (Decentralized Per-Tenant Gateway)

## 1. Executive Summary

This architecture defines an enterprise-grade, multi-tenant B2B Software-as-a-Service (SaaS) platform built on Kubernetes. The platform delivers isolated, customized, and branded **OpenWebUI** workspaces for corporate tenants, backed by **dedicated, per-tenant LiteLLM Proxy** gateways.

Rather than funneling all corporate traffic through a shared centralized proxy gateway, this platform adopts a **Decentralized Per-Tenant Gateway** topology. Each tenant organization resides in an isolated Kubernetes namespace (`tenant-<tenant-id>`) containing both OpenWebUI and its own localized LiteLLM proxy.

This architecture delivers complete failure domain isolation (zero blast radius), eliminates central credential honeypots, prevents cross-tenant prompt cache bleed, enables native **Bring Your Own Key (BYOK)** enterprise integration, and enforces strict zero-trust network boundaries.

---

## 2. Core Architectural Principles

1. **Self-Contained Tenant Workspaces (Namespace-per-Tenant)**
   * Every tenant organization resides in an isolated Kubernetes namespace (`tenant-<tenant-id>`).
   * Each namespace encapsulates:
     1. OpenWebUI frontend & backend deployment.
     2. Dedicated PersistentVolumeClaim for SQLite database (`webui.db`), user profiles, and embeddings.
     3. Dedicated LiteLLM Proxy service (`http://litellm:4000/v1`).
     4. Dedicated ConfigMaps for branding and localized model routing rules.
     5. Dedicated Secrets for LDAPS directory credentials, local session keys, and tenant upstream LLM credentials.
   * Zero cross-tenant data co-mingling, zero shared proxy runtimes, and zero shared caches.

2. **Zero Blast Radius & Failure Containment**
   * An outage, memory leak, or misconfiguration in Tenant A's OpenWebUI or LiteLLM instance has **zero impact** on Tenant B.
   * Eliminates the central gateway Single Point of Failure (SPOF).

3. **Compartmentalized Credentials & First-Class BYOK**
   * Upstream provider credentials (e.g. OpenAI, Anthropic, Azure OpenAI endpoints, AWS Bedrock IAM roles) are stored strictly inside the tenant's namespace Secrets.
   * Supports both SaaS-managed credentials and client **Bring Your Own Key (BYOK)**, enabling enterprises to route traffic through their existing corporate cloud agreements (e.g. Azure HIPAA BAAs).
   * A breach of Tenant A cannot expose Tenant B's credentials.

4. **Independent Enterprise Directory Identity (LDAPS)**
   * Each tenant integrates directly with its corporate directory via secure LDAPS (Lightweight Directory Access Protocol over TLS) or Active Directory.
   * Authentication is enforced at the tenant boundary; one tenant's directory configuration or outage has zero impact on any other tenant.
   * A secure fallback local administrative path is preserved for initial workspace onboarding or emergency break-glass procedures.

5. **Localized Governance & Budget Ceilings**
   * Each tenant's LiteLLM proxy enforces sliding-window rate limits (RPM / TPM) and hard monthly dollar budget ceilings (`max_budget`).
   * When a tenant consumes their allocated budget, the local proxy immediately returns HTTP 400/429 `BudgetExceededError`, terminating requests before contacting upstream cloud providers.

---

## 3. High-Level Architecture Overview

```
                            ┌────────────────────────────────────────────────────────┐
                            │                    Public Internet                     │
                            └───────────────────────────┬────────────────────────────┘
                                                        │ HTTPS (Port 443)
                                                        ▼
                            ┌────────────────────────────────────────────────────────┐
                            │           Ingress Controller (ingress-nginx)           │
                            │       Automatic Subdomain Routing & TLS Offloading     │
                            └───────────┬───────────────────────────────┬────────────┘
                                        │                               │
                      Host: acme.ai.saasdomain.com      Host: globex.ai.saasdomain.com
                                        │                               │
                                        ▼                               ▼
    ┌───────────────────────────────────────────────┐ ┌───────────────────────────────────────────────┐
    │ Namespace: tenant-acme (Isolated Perimeter)   │ │ Namespace: tenant-globex (Isolated Perimeter) │
    │                                               │ │                                               │
    │  ┌─────────────────────────────────────────┐  │ │  ┌─────────────────────────────────────────┐  │
    │  │ OpenWebUI Pod (Acme Corp Branding)      │  │ │  │ OpenWebUI Pod (Globex Branding)         │  │
    │  │  - Env: OPENAI_API_BASE_URL             │  │ │  │  - Env: OPENAI_API_BASE_URL             │  │
    │  │    (http://litellm:4000/v1)             │  │ │  │    (http://litellm:4000/v1)             │  │
    │  │  - LDAPS -> ldaps.corp.acme.com:636     │  │ │  │  - LDAPS -> ldaps.globex.com:636        │  │
    │  └────────────────────┬────────────────────┘  │ │  └────────────────────┬────────────────────┘  │
    │                       │ Local HTTP (:4000)    │ │                       │ Local HTTP (:4000)    │
    │                       ▼                       │ │                       ▼                       │
    │  ┌─────────────────────────────────────────┐  │ │  ┌─────────────────────────────────────────┐  │
    │  │ Dedicated LiteLLM Proxy (Acme)          │  │ │  │ Dedicated LiteLLM Proxy (Globex)        │  │
    │  │  - Local Rate Limits & Hard Budgets     │  │ │  │  - Local Rate Limits & Hard Budgets     │  │
    │  │  - Model Whitelisting & Fallback Router │  │ │  │  - Model Whitelisting & Fallback Router │  │
    │  │  - Acme BYOK / Scoped Provider Keys     │  │ │  │  - Globex Scoped Provider Keys          │  │
    │  └────────────────────┬────────────────────┘  │ │  └────────────────────┬────────────────────┘  │
    │                       │                       │ │                       │                       │
    │         Dedicated PVC │ (webui.db / docs)     │ │         Dedicated PVC │ (webui.db / docs)     │
    │                       ▼                       │ │                       ▼                       │
    │              [ Persistent Volume ]            │ │              [ Persistent Volume ]            │
    │                                               │ │                                               │
    │  [ NetworkPolicy: Deny Cross-Namespace ]      │ │  [ NetworkPolicy: Deny Cross-Namespace ]      │
    └───────────────────────┼───────────────────────┘ └───────────────────────┼───────────────────────┘
                            │ Outbound HTTPS (443)                            │ Outbound HTTPS (443)
                            ▼                                                 ▼
    ┌───────────────────────────────────────────────┐ ┌───────────────────────────────────────────────┐
    │ Upstream LLM APIs (OpenAI / Anthropic)        │ │ Upstream LLM APIs (Azure OpenAI / Bedrock)    │
    └───────────────────────────────────────────────┘ └───────────────────────────────────────────────┘
```

---

## 4. Component Deep Dives

### 4.1 Tenant Namespace Architecture (`tenant-<id>`)

Each tenant namespace is an isolated security boundary containing:
1. **OpenWebUI Deployment**:
   * Runs under non-root security context (`UID 1000`).
   * Configured via environment variables and mounted Kubernetes Secrets.
   * `WEBUI_NAME`: Organization-specific portal name (e.g. `Acme Corp AI Workspace`).
   * `WEBUI_URL`: Canonical subdomain URL (e.g. `https://acme.ai.saasdomain.com`).
   * `OPENAI_API_BASE_URL`: Directed locally inside the namespace to `http://litellm:4000/v1`.
   * `ENABLE_OPENAI_API=true`, `ENABLE_OLLAMA_API=false`.
2. **Dedicated LiteLLM Proxy Deployment & Service**:
   * Runs LiteLLM container pointed to a local `config.yaml` rendered from ConfigMap `openwebui-litellm-config`.
   * ClusterIP service exposing internal port 4000 (`litellm:4000`).
   * Manages model aliases, provider fallbacks (e.g. GPT-4o $\to$ Claude 3.5 Sonnet), and rate limits.
   * Connects to upstream providers using tenant-scoped secrets.
3. **Persistent Storage (PVC)**:
   * Dedicated PersistentVolumeClaim (`openwebui-data-pvc`) using ReadWriteOnce block storage.
   * Houses SQLite database (`webui.db`), user avatars, uploaded documents, and embeddings.
   * Complete physical separation prevents any cross-tenant filesystem or database access.
4. **Enterprise Directory Configuration (LDAPS)**:
   * Injected securely via Kubernetes Secret:
     * `ENABLE_LDAP=true`
     * `LDAP_SERVER_HOST`: Corporate LDAPS server FQDN or IP.
     * `LDAP_SERVER_PORT`: `636` (SSL/TLS) or `389` (StartTLS).
     * `LDAP_USE_TLS=true`
     * `LDAP_SEARCH_BASE`: Base DN (e.g., `OU=Users,OU=AcmeCorp,DC=acme,DC=com`).
     * `LDAP_APP_DN`: Service account bind DN.
     * `LDAP_APP_PASSWORD`: Service account password.
     * `LDAP_ATTRIBUTE_FOR_USERNAME`: `sAMAccountName` (Active Directory) or `uid` (OpenLDAP).
5. **Emergency Break-Glass Admin**:
   * Initial bootstrap admin email and password seeded during provisioning for local administrative recovery if the corporate directory is unreachable.
6. **Resource Guardrails (ResourceQuota & LimitRange)**:
   * Enforces CPU, memory, and storage limits across both pods in the tenant namespace.
   * Default limits: 4 CPU cores, 4 GiB RAM, 20 GiB storage quota.

---

## 5. Zero-Trust Network Security Architecture

The platform enforces strict Kubernetes `NetworkPolicy` objects based on the principle of least privilege:

### 5.1 OpenWebUI Pod Policy
* **Ingress**:
  * Allow TCP traffic on port 8080 **only** from pods bearing label `app.kubernetes.io/name: ingress-nginx` in the `ingress-nginx` namespace.
* **Egress**:
  * **Allow Cluster DNS**: UDP/TCP port 53 to `kube-system`.
  * **Allow Local LiteLLM**: TCP port 4000 to pod labeled `app.kubernetes.io/name: litellm` **within the same namespace**.
  * **Allow Tenant LDAPS**: TCP port 636/389 to the tenant's specified LDAPS server IP/CIDR.
  * **DENY ALL OTHER EGRESS**:
    * Direct egress to public Internet LLMs is dropped (all inference must traverse the local proxy).
    * Direct egress to other tenant namespaces is dropped.
    * Direct egress to link-local metadata (`169.254.169.254/32`) is dropped.

### 5.2 LiteLLM Pod Policy
* **Ingress**:
  * Allow TCP port 4000 **only** from the local OpenWebUI pod in the same namespace (`podSelector.matchLabels.app.kubernetes.io/name: openwebui`).
* **Egress**:
  * **Allow Cluster DNS**: UDP/TCP port 53 to `kube-system`.
  * **Allow Outbound HTTPS**: TCP port 443 to public Internet CIDRs (`0.0.0.0/0` except private RFC 1918 and link-local `169.254.169.254/32`).
  * **DENY ALL OTHER EGRESS**:
    * Dropped to other tenant namespaces.

---

## 6. End-to-End Request Flows

### 6.1 Tenant User Chat Flow
1. **User Request**: User visits `https://acme.ai.saasdomain.com` in their browser.
2. **TLS Offloading & Ingress**: Ingress controller terminates TLS using the tenant's certificate and routes HTTP to `openwebui:8080`.
3. **Authentication**: OpenWebUI validates session cookie. If unauthenticated, user credentials are authenticated against `ldaps.corp.acme.com:636`.
4. **Chat Generation**: User submits prompt in OpenWebUI.
5. **Local Gateway Proxying**: OpenWebUI sends HTTP POST to `http://litellm:4000/v1/chat/completions`.
6. **Local Governance**: Local LiteLLM verifies rate limits (RPM/TPM), model whitelisting, and current spend against `max_budget`.
7. **Upstream Execution**: Local LiteLLM attaches tenant-scoped API key and invokes upstream provider over HTTPS (port 443).
8. **Streaming Delivery**: Response streams back through local LiteLLM to OpenWebUI and is rendered to the user via Server-Sent Events (SSE).

### 6.2 Budget Cutoff Flow
1. User submits prompt when local cumulative spend meets `max_budget`.
2. Local LiteLLM proxy detects budget limit reached and immediately rejects request with HTTP 400/429 (`BudgetExceededError`).
3. Upstream LLM provider is never contacted (zero unexpected cost).
4. OpenWebUI displays notification indicating organizational budget limit reached.

---

## 7. Lifecycle & Operational Management

Tenant lifecycle is orchestrated via the `tenant-manager` engine:
* **Provisioning**: Single-command idempotent creation of tenant namespace, ResourceQuota, LimitRange, PVC, OpenWebUI branding, LiteLLM config, unified credentials Secret, OpenWebUI deployment, LiteLLM deployment, and Ingress.
* **Monitoring & Auditing**: Direct CLI status commands reporting local spend, remaining budget, pod health, and directory connectivity.
* **Deprovisioning**: Atomic deletion of tenant namespace, instantly terminating proxy keys, reclaiming compute, and disposing or archiving data.
