# Architecture & Trade-Off Analysis

## Executive Overview

Designing an enterprise B2B SaaS platform for hosting AI chat workspaces requires balancing three competing architectural vectors:
1. **Security & Regulatory Compliance**: Preventing cross-tenant data leakage, securing upstream credentials, enforcing tenant network boundaries, and complying with SOC2, HIPAA, and GDPR standards.
2. **Operational Simplicity & Reliability**: Maintaining predictable provisioning, deterministic rollbacks, straightforward troubleshooting, and low administrative overhead.
3. **Resource & Financial Efficiency**: Managing compute, memory, and LLM token expenditures while providing high availability.

This document evaluates the key architectural trade-offs across **Tenancy Isolation Models**, **LLM Gateway Architectures**, **Storage Architectures**, and **Provisioning Mechanisms**, and details the rationale behind our chosen production design.

---

## 1. Tenancy Isolation Models

We analyzed three primary models for running OpenWebUI in a multi-tenant B2B environment:

| Criteria | Option A: Shared OpenWebUI Instance | Option B: Namespace-per-Tenant (Chosen) | Option C: Virtual Cluster (vCluster) |
| :--- | :--- | :--- | :--- |
| **Data Separation** | ❌ **High Risk**: Single database; chats, documents, and users share tables without row-level tenancy. | ✅ **Complete**: Dedicated volume or database per tenant. No data co-mingling. | ✅ **Complete**: Dedicated API server and storage per tenant. |
| **Network Isolation** | ❌ **None**: All tenants share the same network runtime and pod IP. | ✅ **Strict**: Enforced via Kubernetes `NetworkPolicy` (Calico/Cilium). Zero cross-talk. | ✅ **Strict**: Full network isolation. |
| **Enterprise Identity** | ❌ **Impossible**: Single instance cannot bind to multiple distinct corporate LDAPS servers. | ✅ **Native**: Each tenant deploys its own LDAPS client pointed to its directory. | ✅ **Native**: Dedicated directory configuration. |
| **Custom Branding** | ❌ **Global**: All users see the same logo, CSS, and portal name. | ✅ **Full**: Custom CSS, logos, splash text, and model lists per tenant. | ✅ **Full**: Complete customization. |
| **Blast Radius** | ❌ **Cluster-wide**: A rogue query or crash impacts all corporate clients. | ✅ **Contained**: Issues in Tenant A's pod never impact Tenant B. | ✅ **Contained**: Independent virtual control plane. |
| **Resource Overhead** | ✅ **Minimal**: Single container set (~500MB RAM total). | ⚖️ **Moderate**: Dedicated container per tenant (~300–600MB RAM per tenant). | ❌ **Heavy**: Virtual K8s API server, controller-manager, and etcd per tenant. |
| **Operational Overhead**| ⚖️ Low initial setup, nightmare governance. | ✅ **Low/Automated**: Managed cleanly via standardized Helm charts and CLI. | ❌ **High**: Complex nested cluster lifecycle, multi-layer networking. |

### Justification for Option B (Namespace-per-Tenant)
OpenWebUI is fundamentally an application designed for single-organization deployment. Attempting to force multi-tenancy inside a single OpenWebUI instance fails on critical enterprise requirements:
1. OpenWebUI administrators can view all users and global conversation data in that instance.
2. Enterprises require binding to their private LDAPS / Active Directory forests (`ldaps://corp.acme.com`), which requires distinct bind credentials, base DNs, and CA certificates.
3. Option C (vCluster) provides complete isolation but introduces excessive control-plane overhead and nested networking complexity that is unnecessary for hosting a stateless web frontend.

Therefore, **Namespace-per-Tenant** provides the ideal balance: genuine cryptographic and network isolation, tenant-specific identity, and acceptable resource footprints easily managed with modern Kubernetes density.

---

## 2. LLM Gateway Architectures: Central Gateway vs. Per-Tenant Gateway

We evaluated two architectural topologies for positioning the LiteLLM Proxy Gateway:

| Architectural Vector | Model A: Centralized LiteLLM Gateway | Model B: Per-Tenant LiteLLM Gateway (Chosen) |
| :--- | :--- | :--- |
| **Blast Radius / Failure Domain** | ❌ **Single Point of Failure (SPOF)**: Outage in central gateway brings down all corporate tenants. | ✅ **Zero Blast Radius**: An issue or memory leak in Tenant A's proxy has zero impact on Tenant B. |
| **Credential Honeypot** | ❌ **High Risk**: Central namespace holds all upstream master keys across the entire business. | ✅ **Compartmentalized**: Credentials are strictly scoped inside `tenant-<id>` secrets. |
| **BYOK (Bring Your Own Key)** | ⚖️ Complex: Requires multi-tenant credential mapping and dynamic routing in central proxy. | ✅ **Native & First-Class**: Tenant configures their private Azure OpenAI, AWS Bedrock, or OpenAI keys locally. |
| **Cache & Log Confidentiality** | ⚠️ Risk of prompt cache bleed across tenants if exact-match caching key hashing fails. | ✅ **Physically Isolated**: Dedicated in-memory or SQLite cache per tenant; zero cross-tenant cache hit risk. |
| **Custom Routing Policies** | ⚖️ Shared configuration file churn; global deployment updates for individual tenant changes. | ✅ **Decentralized**: Each tenant defines independent model aliases, fallbacks, and local rate limits. |
| **Cross-Tenant Network Flow**| Requires tenant pods to route across namespaces to `litellm` control plane. | ✅ **Intra-Namespace Only**: OpenWebUI talks to `http://litellm:4000/v1` locally within its own namespace. |
| **Resource Overhead** | ✅ Minimal (~2–4 GB for shared cluster). | ⚖️ Moderate (~100m CPU, 128–256 MiB RAM per tenant proxy pod). |

### Justification for Model B (Per-Tenant LiteLLM Gateway)
In enterprise B2B environments, data privacy and failure isolation outweigh minimal resource savings:
1. **BYOK Compliance**: Enterprise customers frequently demand using their existing enterprise agreements (e.g., direct Azure OpenAI instances under corporate HIPAA/BAA contracts). A per-tenant LiteLLM instance allows clients to input their corporate credentials directly into their isolated namespace.
2. **Zero Blast Radius**: Upstream rate-limit throttling or bad prompts from one tenant cannot destabilize other tenants.
3. **Network Boundary Simplicity**: OpenWebUI never needs egress permissions to another namespace. All chat traffic stays strictly within the tenant's namespace perimeter before the local proxy egresses to upstream APIs.

---

## 3. Storage Architectures

OpenWebUI requires persistent state for user accounts, session history, file uploads, document embeddings, and model settings. We evaluated three persistence strategies:

| Storage Strategy | Data Isolation | Multi-Replica HA | Backup & GDPR Teardown | Operational Complexity | Cost |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Standalone SQLite on Dedicated PVC (Chosen Default)** | 🟢 Cryptographic / Volume level | 🟡 Active-Passive (1 replica with PVC failover) | 🟢 **Trivial**: Delete/Snapshot PVC for instant compliance | 🟢 **Zero**: Self-contained in pod | 🟢 Low (standard cloud block storage) |
| **2. Shared Multi-Tenant PostgreSQL Instance** | 🔴 Logical isolation (separate databases on same host) | 🟢 Active-Active multi-replica | 🟡 Requires tenant database DROP and logical pg_dump | 🟡 Medium (manage shared Postgres cluster) | 🟢 Shared database compute cost |
| **3. Dedicated Cloud SQL / Managed Postgres per Tenant** | 🟢 Complete physical / instance isolation | 🟢 Active-Active multi-replica | 🟢 Native snapshot & drop | 🔴 High (costly per-instance minimums) | 🔴 High ($50–$100+/mo per tenant base cost) |

### Recommended Production Storage Design
* **Default Tier (Cost-Effective / Standard Isolation)**: **Standalone SQLite on a Dedicated PVC (`ReadWriteOnce`)**.
  * Each tenant receives a dedicated PersistentVolumeClaim backed by high-IOPS block storage (e.g. AWS EBS gp3, GCP pd-ssd, or Ceph RBD).
  * GDPR "Right to be Forgotten" and offboarding compliance is absolute: deleting the PVC permanently purges all tenant chat logs and files without complex database scrubbing.
  * Backups are taken as atomic storage snapshots (`VolumeSnapshot`).
* **Enterprise High-Availability Tier (Multi-Replica HA)**:
  * For enterprise tenants requiring horizontal pod autoscaling (2+ OpenWebUI replicas), the chart supports configuring `database.type: postgresql` with a dedicated database and user credentials on a shared high-availability PostgreSQL cluster (e.g. AWS Aurora or CloudNative-PG), ensuring zero data sharing between database roles.

---

## 4. Provisioning & Lifecycle Mechanisms

We evaluated three mechanisms for orchestrating tenant onboarding, updates, and deprovisioning:

| Feature / Criteria | 1. Custom Kubernetes Operator | 2. Pure GitOps (ArgoCD / Flux) | 3. Automated Orchestration Engine (`tenant-manager`) (Chosen) |
| :--- | :--- | :--- | :--- |
| **Execution Synchronicity** | Asynchronous (Eventual consistency) | Asynchronous (Git polling / webhook) | **Synchronous**: Direct feedback to provisioning caller |
| **Atomic Rollback** | Hard (Must implement two-phase commit inside reconciliation loop) | No automatic rollback of external state | **Built-in**: Transaction manager rolls back namespaces on rollout failure |
| **GitOps Compatibility** | Low | Native | **Hybrid**: Generates GitOps-ready manifests and charts while supporting direct CLI automation |
| **Developer / SRE UX** | Requires cluster-wide CRD and controller installation | Requires Git commit, PR merge, and sync wait | Single CLI command: `tenant-manager provision acme.yaml` with instant status |

---

## 5. Summary Decision Matrix

| Dimension | Selected Architecture | Key Reason |
| :--- | :--- | :--- |
| **Tenancy Boundary** | **Namespace-per-Tenant** | Uncompromising security, independent LDAPS, zero cross-tenant data leakage. |
| **LLM Gateway** | **Per-Tenant LiteLLM Proxy** | Zero blast radius, native BYOK credential compartmentalization, zero prompt cache bleed. |
| **Storage** | **Dedicated PVC (SQLite) / Dedicated DB** | Complete data compartmentalization, instant GDPR compliance. |
| **Networking** | **Zero-Trust NetworkPolicies** | Default-deny; OpenWebUI routes only to local LiteLLM:4000; LiteLLM egresses to upstream LLMs. |
| **Lifecycle** | **Transactional Orchestrator (`tenant-manager`)** | Atomic end-to-end provisioning with rollback of complete tenant stack. |
