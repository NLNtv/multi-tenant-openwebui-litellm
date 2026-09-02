"""
Tenant Specification Data Models
Defines Pydantic v2 schemas for multi-tenant configuration and validation.
"""

from __future__ import annotations
import re
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class MetadataConfig(BaseModel):
    tenant_id: str = Field(
        ...,
        alias="tenantId",
        description="Unique alphanumeric slug used for namespace and resource naming."
    )
    tenant_name: str = Field(
        ...,
        alias="tenantName",
        description="Full display name of the corporate organization."
    )
    contact_email: str = Field(
        ...,
        alias="contactEmail",
        description="Administrative contact email for billing and governance."
    )
    environment: str = Field(
        default="production",
        description="Target deployment environment (production, staging, development)."
    )

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, v: str) -> str:
        pattern = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
        if not re.match(pattern, v):
            raise ValueError(
                f"tenantId '{v}' must be lowercase alphanumeric and may contain hyphens, "
                "conforming to RFC 1123 DNS label standards."
            )
        if len(v) > 40:
            raise ValueError(f"tenantId '{v}' exceeds maximum length of 40 characters.")
        return v


class RoutingConfig(BaseModel):
    subdomain: str = Field(
        ...,
        description="Unique subdomain slug (e.g. 'acme' -> 'acme.ai.domain.com')."
    )
    base_domain: str = Field(
        default="ai.saasdomain.com",
        alias="baseDomain",
        description="Parent SaaS domain."
    )
    tls_cluster_issuer: str = Field(
        default="letsencrypt-prod",
        alias="tlsClusterIssuer",
        description="Cert-manager cluster issuer name for TLS certificates."
    )

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        pattern = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Subdomain '{v}' must be lowercase alphanumeric and may contain hyphens."
            )
        reserved = {"admin", "api", "litellm", "gateway", "system", "metrics", "health", "auth"}
        if v.lower() in reserved:
            raise ValueError(f"Subdomain '{v}' is a reserved platform keyword.")
        return v

    @property
    def fqdn(self) -> str:
        return f"{self.subdomain}.{self.base_domain}"


class GovernanceConfig(BaseModel):
    max_budget_usd: float = Field(
        ...,
        alias="maxBudgetUsd",
        gt=0,
        description="Hard spending limit in USD before request cutoff is enforced."
    )
    budget_duration: str = Field(
        default="30d",
        alias="budgetDuration",
        description="Reset period for budget limit (e.g. '30d', '7d', '24h')."
    )
    rpm_limit: int = Field(
        default=120,
        alias="rpmLimit",
        gt=0,
        description="Requests Per Minute rate limit."
    )
    tpm_limit: int = Field(
        default=100000,
        alias="tpmLimit",
        gt=0,
        description="Tokens Per Minute rate limit."
    )
    allowed_models: List[str] = Field(
        ...,
        alias="allowedModels",
        min_length=1,
        description="Whitelist of LLM models permitted for this tenant."
    )

    @field_validator("budget_duration")
    @classmethod
    def validate_duration(cls, v: str) -> str:
        if not re.match(r"^\d+[dhm]$", v):
            raise ValueError(f"budgetDuration '{v}' must be in format '<num>d', '<num>h', or '<num>m'.")
        return v


class UpstreamCredentialsConfig(BaseModel):
    """Credentials for tenant-specific or BYOK upstream model providers."""
    openai_api_key: Optional[str] = Field(default="", alias="openaiApiKey")
    anthropic_api_key: Optional[str] = Field(default="", alias="anthropicApiKey")
    azure_openai_api_key: Optional[str] = Field(default="", alias="azureOpenaiApiKey")
    azure_openai_endpoint: Optional[str] = Field(default="", alias="azureOpenaiEndpoint")
    aws_access_key_id: Optional[str] = Field(default="", alias="awsAccessKeyId")
    aws_secret_access_key: Optional[str] = Field(default="", alias="awsSecretAccessKey")
    aws_region_name: Optional[str] = Field(default="us-east-1", alias="awsRegionName")


class BrandingConfig(BaseModel):
    portal_title: str = Field(
        ...,
        alias="portalTitle",
        description="Custom portal header title displayed in OpenWebUI."
    )
    default_model: str = Field(
        ...,
        alias="defaultModel",
        description="Default model pre-selected in the user chat interface."
    )
    custom_logo_url: Optional[str] = Field(
        default="",
        alias="customLogoUrl",
        description="URL to custom organization logo."
    )
    custom_css: Optional[str] = Field(
        default="",
        alias="customCss",
        description="Custom CSS styling rules for theme overrides."
    )


class IdentityConfig(BaseModel):
    ldap_enabled: bool = Field(
        default=True,
        alias="ldapEnabled",
        description="Whether LDAP/LDAPS directory authentication is active."
    )
    server_host: str = Field(
        ...,
        alias="serverHost",
        description="Host FQDN or IP of the enterprise LDAPS / Active Directory server."
    )
    server_port: int = Field(
        default=636,
        alias="serverPort",
        ge=1,
        le=65535,
        description="Directory connection port (typically 636 for LDAPS, 389 for StartTLS)."
    )
    use_tls: bool = Field(
        default=True,
        alias="useTls",
        description="Enforce SSL/TLS encryption for directory queries."
    )
    validate_cert: bool = Field(
        default=True,
        alias="validateCert",
        description="Enforce CA certificate validation on the directory server."
    )
    search_base: str = Field(
        ...,
        alias="searchBase",
        description="Base Distinguished Name (DN) for user discovery (e.g. 'OU=Users,DC=acme,DC=com')."
    )
    bind_dn: str = Field(
        ...,
        alias="bindDn",
        description="Service account bind DN used to perform directory queries."
    )
    bind_password: str = Field(
        ...,
        alias="bindPassword",
        description="Service account password for directory binding."
    )
    username_attribute: str = Field(
        default="sAMAccountName",
        alias="usernameAttribute",
        description="Directory attribute representing the username ('sAMAccountName' for AD, 'uid' for OpenLDAP)."
    )
    search_filter: Optional[str] = Field(
        default="(&(objectClass=user))",
        alias="searchFilter",
        description="LDAP filter query to restrict authorized user membership."
    )
    ca_cert_pem: Optional[str] = Field(
        default="",
        alias="caCertPem",
        description="Optional PEM certificate string for private enterprise CA trust."
    )


class AdminFallbackConfig(BaseModel):
    enabled: bool = Field(
        default=True,
        description="Allow local bootstrap administrator fallback account."
    )
    email: str = Field(
        ...,
        description="Bootstrap administrator email address."
    )
    password: str = Field(
        ...,
        min_length=12,
        description="Bootstrap administrator password (minimum 12 characters)."
    )


class ComputeConfig(BaseModel):
    replicas: int = Field(default=1, ge=1, le=10)
    storage_size: str = Field(default="10Gi", alias="storageSize")
    storage_class: str = Field(default="", alias="storageClass")
    cpu_request: str = Field(default="250m", alias="cpuRequest")
    cpu_limit: str = Field(default="2000m", alias="cpuLimit")
    memory_request: str = Field(default="512Mi", alias="memoryRequest")
    memory_limit: str = Field(default="2Gi", alias="memoryLimit")


class TenantSpecification(BaseModel):
    api_version: str = Field(
        default="saas.platform.io/v1alpha1",
        alias="apiVersion"
    )
    kind: str = Field(
        default="TenantSpecification"
    )
    metadata: MetadataConfig
    routing: RoutingConfig
    governance: GovernanceConfig
    branding: BrandingConfig
    identity: IdentityConfig
    upstream_credentials: UpstreamCredentialsConfig = Field(
        default_factory=UpstreamCredentialsConfig,
        alias="upstreamCredentials"
    )
    admin_fallback: AdminFallbackConfig = Field(
        default_factory=lambda: AdminFallbackConfig(
            email="admin@tenant.local",
            password="TemporaryPassword123!"
        ),
        alias="adminFallback"
    )
    compute: ComputeConfig = Field(
        default_factory=ComputeConfig
    )

    @model_validator(mode="after")
    def validate_cross_field_rules(self) -> TenantSpecification:
        # Verify default model is contained in allowed models list
        if self.branding.default_model not in self.governance.allowed_models:
            raise ValueError(
                f"Branding defaultModel '{self.branding.default_model}' "
                f"must be included in governance.allowedModels: {self.governance.allowed_models}"
            )
        return self
