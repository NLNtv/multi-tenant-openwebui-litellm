# System Architecture: Multi-Tenant OpenWebUI SaaS on Kubernetes with LiteLLM Gateway

## 1. Executive Summary

This architecture defines an enterprise-grade, multi-tenant B2B Software-as-a-Service (SaaS) platform built on Kubernetes. The platform delivers isolated, customized, and branded **OpenWebUI** instances for corporate tenants, backed by a centralized, metered, and highly available **LiteLLM Proxy** gateway.

The platform resolves the fundamental challenge of serving enterprise AI chat interfaces: providing corporations with independent workspaces, custom enterprise identity integration (Active Directory / LDAPS), and dedicated data persistence, while maintaining centralized governance, financial budget cutoffs, unified provider routing, and zero leakage of upstream API keys.

---

## 2. Core Architectural Principles

1. **Hard Tenant Isolation (Namespace-per-Tenant)**
   * Every tenant organization resides in an isolated Kubernetes namespace (`tenant-<tenant-id>`).
   * No multi-tenant data co-mingling: User chats, files, embeddings, and credentials reside in isolated volumes or databases per tenant.
   * Default-deny zero-trust NetworkPolicies prevent any cross-tenant pod-to-pod network connectivity.

2. **Compartmentalized Upstream Credentials**
   * Tenants never receive or configure raw upstream API keys (e.g., OpenAI, Anthropic, Bedrock, Vertex AI).
   * Upstream provider keys exist exclusively inside the central LiteLLM Gateway in the `litellm` control plane namespace.
   * Each tenant instance communicates with the LiteLLM Proxy using an isolated **Virtual Key** (`sk-tenant-<hash>`) with strictly enforced token, rate, and dollar spending limits.

3. **Autonomous Enterprise Identity**
   * Each tenant integrates directly with its corporate directory via secure LDAPS (Lightweight Directory Access Protocol over TLS) or Active Directory.
   * Authentication happens at the tenant boundary; one tenant's directory configuration or outage has zero impact on any other tenant.
   * A secure fallback local administrative path is preserved for initial workspace onboarding or emergency break-glass procedures.

4. **Centralized Financial & Operational Governance**
   * The LiteLLM Gateway tracks usage per virtual key in real time.
   * When a tenant consumes their monthly or allocated budget (`max_budget`), requests are automatically cut off with immediate HTTP 400/429 enforcement.
   * Central administrators maintain global visibility over LLM spend, token throughput (TPM), request rates (RPM), and upstream provider latency.

5. **Declarative Lifecycle Automation**
   * End-to-end tenant provisioning, updates, and deprovisioning are driven through a validated configuration payload (`tenant-spec.yaml`) handled by the `tenant-manager` orchestration engine with atomic rollback capabilities.

---

## 3. High-Level Architecture Overview

The system is partitioned into three primary tiers:

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
                      Host: acme.ai.saas.com            Host: globex.ai.saas.com
                                        │                               │
                                        ▼                               ▼
    ┌───────────────────────────────────────────────┐ ┌───────────────────────────────────────────────┐
    │ Namespace: tenant-acme                        │ │ Namespace: tenant-globex                      │
    │  ┌─────────────────────────────────────────┐  │ │  ┌─────────────────────────────────────────┐  │
    │  │ OpenWebUI Pod (Acme Corp Branding)      │  │ │  │ OpenWebUI Pod (Globex Branding)         │  │
    │  │  - Env: OPENAI_API_BASE_URL (LiteLLM)   │  │ │  │  - Env: OPENAI_API_BASE_URL (LiteLLM)   │  │
    │  │  - Env: OPENAI_API_KEY (Virtual Key A)  │  │ │  │  - Env: OPENAI_API_KEY (Virtual Key B)  │  │
    │  │  - LDAPS Client -> ldaps.acme.com:636   │  │ │  │  - LDAPS Client -> ldaps.globex.com:636 │  │
    │  └────────────────────┬────────────────────┘  │ │  └────────────────────┬────────────────────┘  │
    │                       │                       │ │                       │                       │
    │         Dedicated PVC │ (webui.db / docs)     │ │         Dedicated PVC │ (webui.db / docs)     │
    │                       ▼                       │ │                       ▼                       │
    │              [ Persistent Volume ]            │ │              [ Persistent Volume ]            │
    │                                               │ │                                               │
    │  [ NetworkPolicy: Deny Cross-Namespace ]      │ │  [ NetworkPolicy: Deny Cross-Namespace ]      │
    └───────────────────────┼───────────────────────┘ └───────────────────────┼───────────────────────┘
                            │                                                 │
                            │ Allowed Egress: HTTP Port 4000                  │ Allowed Egress: HTTP Port 4000
                            └───────────────────────┬─────────────────────────┘
                                                    ▼
    ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
    │ Namespace: litellm (Central LLM Gateway)                                                        │
    │                                                                                                 │
    │  ┌───────────────────────────────────────────────────────────────────────────────────────────┐  │
    │  │ LiteLLM Proxy Service (ClusterIP: litellm.litellm.svc.cluster.local:4000)                  │  │
    │  │  - Virtual Key Validator & Budget Metering                                                │  │
    │  │  - Dynamic Model Routing & Fallback Policies (OpenAI -> Anthropic -> Bedrock)             │  │
    │  │  - RPM / TPM Rate Limiting                                                                │  │
    │  └────────────────────┬───────────────────────────────────────────────┬──────────────────────┘  │
    │                       │                                               │                         │
    │                       ▼                                               ▼                         │
    │         ┌───────────────────────────┐                   ┌───────────────────────────┐           │
    │         │ PostgreSQL Database       │                   │ Redis Distributed Cache   │           │
    │         │ (Key state, spend, logs)  │                   │ (Rate limits, token cache)│           │
    │         └───────────────────────────┘                   └───────────────────────────┘           │
    │                                                                                                 │
    │  [ NetworkPolicy: Allow Ingress from tenant-* on 4000; Allow Egress to Public Cloud LLMs ]      │
    └───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                    │ Outbound HTTPS (443)
                                                    ▼
                            ┌────────────────────────────────────────────────────────┐
                            │           Upstream LLM Provider APIs                   │
                            │      (OpenAI / Anthropic / Bedrock / Vertex AI)        │
                            └────────────────────────────────────────────────────────┘
```

---

## 4. Component Deep Dives

### 4.1 Tenant Namespace Architecture (`tenant-<id>`)

Each tenant namespace is an isolated security boundary containing:
1. **OpenWebUI Deployment**:
   * Runs the official OpenWebUI container under a non-root user (`UID 1000`).
   * Configured via environment variables and mounted Kubernetes Secrets.
   * `WEBUI_NAME`: Organization-specific portal name (e.g. `Acme Corp AI Workspace`).
   * `WEBUI_URL`: Canonical subdomain URL (e.g. `https://acme.ai.company.com`).
   * `OPENAI_API_BASE_URL`: Directed strictly to `http://litellm.litellm.svc.cluster.local:4000/v1`.
   * `OPENAI_API_KEY`: Injected from Secret `litellm-tenant-key` containing the tenant's isolated virtual key.
   * `ENABLE_OPENAI_API=true`, `ENABLE_OLLAMA_API=false`.
2. **Persistent Storage (PVC)**:
   * Dedicated PersistentVolumeClaim (`openwebui-data-pvc`) using ReadWriteOnce storage.
   * Houses the tenant's SQLite database (`webui.db`), user avatars, uploaded documents, and vector embeddings.
   * Zero shared storage between tenants ensures zero risk of filesystem traversal or database query pollution.
3. **Enterprise Directory Configuration (LDAPS)**:
   * Injected securely via Kubernetes Secret:
     * `ENABLE_LDAP=true`
     * `LDAP_SERVER_HOST`: Corporate LDAPS server FQDN or IP.
     * `LDAP_SERVER_PORT`: `636` (SSL/TLS) or `389` (StartTLS).
     * `LDAP_USE_TLS=true`
     * `LDAP_SEARCH_BASE`: Base DN (e.g., `OU=Users,OU=AcmeCorp,DC=acme,DC=com`).
     * `LDAP_APP_DN`: Service account bind DN.
     * `LDAP_APP_PASSWORD`: Service account password.
     * `LDAP_ATTRIBUTE_FOR_USERNAME`: `sAMAccountName` (Active Directory) or `uid` (OpenLDAP).
     * `LDAP_SEARCH_FILTERS`: Optional group filter (e.g., `(&(objectClass=user)(memberOf=CN=AIAccess,OU=Groups,DC=acme,DC=com))`).
   * Optional custom CA certificate mounted to `/app/backend/certs/ldap_ca.crt` with `LDAP_CA_CERT_FILE` for corporate PKI trust.
4. **Emergency Break-Glass Admin**:
   * `WEBUI_AUTH=true` enables authentication enforcement.
   * Initial bootstrap admin email and password can be seeded during provisioning for local administrative recovery if the corporate directory is unreachable.
5. **Resource Guardrails (ResourceQuota & LimitRange)**:
   * Prevents noisy neighbors from exhausting node CPU, memory, or storage.
   * Default limits: 2 CPU cores, 4 GiB RAM, 20 GiB storage quota.

### 4.2 Central LLM Gateway (`litellm`)

The LiteLLM Gateway acts as the control plane for all upstream model access:
1. **LiteLLM Proxy Deployment**:
   * Highly available, horizontally autoscaling deployment.
   * Listens on internal port 4000.
   * Stateless request processing; all state resides in PostgreSQL and Redis.
2. **PostgreSQL Key & Spend Store**:
   * Stores virtual keys, spend records, user allocations, and model configurations.
   * Provides audit logging and transactional updates to spend tracking.
3. **Redis Caching & Rate Limiting**:
   * High-speed Redis instance for sliding-window RPM/TPM rate limits.
   * Optional exact-match prompt caching to reduce token spend and latency.
4. **Router & Fallback Policies**:
   * Defines model alias mapping (e.g., `gpt-4o` routes to primary provider with automatic fallback to secondary provider or Claude 3.5 Sonnet if 5xx errors or provider outages occur).

---

## 5. Zero-Trust Network Security Architecture

The platform enforces strict Kubernetes `NetworkPolicy` objects based on the principle of least privilege:

### 5.1 Tenant Namespace NetworkPolicy
* **Ingress**:
  * Block all direct external and internal traffic by default.
  * Allow TCP traffic on port 8080 **only** from pods bearing the label `app.kubernetes.io/name: ingress-nginx` in the `ingress-nginx` namespace.
* **Egress**:
  * **Allow Cluster DNS**: UDP/TCP port 53 to `kube-system`.
  * **Allow LiteLLM Gateway**: TCP port 4000 to namespace `litellm` (selected by `app.kubernetes.io/name: litellm-proxy`).
  * **Allow Tenant LDAPS**: TCP port 636/389 to the tenant's specified LDAPS server IP/CIDR.
  * **DENY ALL OTHER EGRESS**:
    * Direct egress to other tenant namespaces (`tenant-*`) is dropped.
    * Direct egress to the cloud provider instance metadata service (`169.254.169.254`) is dropped.
    * Direct egress to Kubernetes API server (`kubernetes.default`) is dropped.
    * Direct egress to public internet LLMs is dropped (tenants cannot bypass the proxy).

### 5.2 LiteLLM Namespace NetworkPolicy
* **Ingress**:
  * Allow TCP port 4000 from any pod labeled `app.kubernetes.io/part-of: openwebui-tenant`.
  * Allow TCP port 4000 from administrative management pods / CI/CD runner.
* **Egress**:
  * Allow TCP port 5432 to PostgreSQL (`litellm-postgres`).
  * Allow TCP port 6379 to Redis (`litellm-redis`).
  * Allow UDP/TCP port 53 to `kube-system` (DNS).
  * Allow Outbound HTTPS port 443 to public internet CIDRs (OpenAI, Anthropic, AWS Bedrock, Google Cloud Vertex).
  * Drop traffic to tenant namespaces.

---

## 6. End-to-End Request Flows

### 6.1 Tenant User Chat Flow
1. **User Request**: User visits `https://acme.ai.saas.com` in their browser.
2. **TLS Offloading & Ingress**: Ingress controller terminates TLS using the tenant's certificate and routes HTTP to `openwebui.tenant-acme.svc.cluster.local:8080`.
3. **Authentication**: OpenWebUI validates the session cookie. If unauthenticated, user submits credentials which OpenWebUI validates via direct LDAPS query to `ldaps.acme.com:636`.
4. **Chat Generation**: User submits prompt in OpenWebUI.
5. **Gateway Routing**: OpenWebUI backend initiates an HTTP POST to `http://litellm.litellm.svc.cluster.local:4000/v1/chat/completions`, attaching `Authorization: Bearer sk-tenant-acme-prod`.
6. **Governance & Metering**:
   * LiteLLM checks Redis to verify tenant RPM/TPM rate limits.
   * LiteLLM checks PostgreSQL to verify current spend is below `max_budget`.
   * LiteLLM verifies the requested model is permitted under the tenant's model whitelist.
7. **Upstream Execution**: LiteLLM attaches the master provider key (e.g., Anthropic API key) and invokes the provider endpoint over HTTPS.
8. **Spend Accounting**: As response tokens stream back, LiteLLM calculates cost using token counters and increments tenant spend in PostgreSQL.
9. **Streaming Delivery**: Response streams back to OpenWebUI and is rendered to the user via Server-Sent Events (SSE).

### 6.2 Budget Cutoff Flow
1. Tenant submits prompt when remaining balance is insufficient or spend has reached `max_budget`.
2. LiteLLM Proxy intercepts request at the gateway filter.
3. LiteLLM detects `current_spend >= max_budget` and rejects request immediately with HTTP 400/429 (`BudgetExceededError: Key max budget has been exceeded`).
4. Upstream LLM provider is never contacted (zero cost incurred).
5. OpenWebUI displays clear error notification to user indicating organization workspace budget limit reached.

---

## 7. Lifecycle & Operational Management

Tenant lifecycle is orchestrated via the `tenant-manager` engine:
* **Provisioning**: Single-command idempotent creation of LiteLLM virtual key, Kubernetes namespace, secrets, branding, and deployment.
* **Monitoring & Auditing**: Direct CLI status commands reporting spend, remaining budget, pod health, and directory connectivity.
* **Deprovisioning**: Revocation of virtual key in LiteLLM, graceful pod teardown, and configurable data retention/archival of PVC storage.
