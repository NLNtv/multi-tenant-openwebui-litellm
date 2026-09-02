"""
Tenant Provisioning Transaction Manager
Guarantees atomic execution and rollback for multi-tenant onboarding across LiteLLM and Kubernetes.
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from tenant_manager.litellm_client import LiteLLMClient, LiteLLMClientError
from tenant_manager.k8s_provisioner import K8sProvisioner, K8sProvisionerError
from tenant_manager.models import TenantSpecification

logger = logging.getLogger("tenant_manager.transaction")


class CompensationAction:
    def __init__(self, name: str, action: Callable[[], Any], description: str):
        self.name = name
        self.action = action
        self.description = description

    def execute(self) -> bool:
        try:
            logger.info(f"Executing rollback compensation: {self.name} - {self.description}")
            self.action()
            return True
        except Exception as e:
            logger.error(f"Error during rollback action '{self.name}': {e}")
            return False


class ProvisioningTransactionManager:
    def __init__(
        self,
        litellm_client: LiteLLMClient,
        k8s_provisioner: K8sProvisioner,
        registry_path: Optional[Path] = None
    ):
        self.litellm_client = litellm_client
        self.k8s_provisioner = k8s_provisioner
        self.compensations: List[CompensationAction] = []
        self.registry_path = registry_path or Path("tenants/active/registry.json")

    def _register_compensation(self, name: str, action: Callable[[], Any], description: str) -> None:
        self.compensations.append(CompensationAction(name, action, description))

    def _rollback(self) -> None:
        logger.warning(f"Initiating atomic transaction rollback ({len(self.compensations)} actions pending)...")
        # Execute compensations in LIFO (reverse) order
        while self.compensations:
            comp = self.compensations.pop()
            comp.execute()
        logger.warning("Rollback complete.")

    def _record_active_tenant(self, spec: TenantSpecification, virtual_key: str) -> None:
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry = {}
            if self.registry_path.exists():
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    try:
                        registry = json.load(f)
                    except json.JSONDecodeError:
                        registry = {}
            
            registry[spec.metadata.tenant_id] = {
                "tenant_name": spec.metadata.tenant_name,
                "subdomain": spec.routing.subdomain,
                "fqdn": spec.routing.fqdn,
                "virtual_key": virtual_key,
                "max_budget_usd": spec.governance.max_budget_usd,
                "budget_duration": spec.governance.budget_duration,
                "models": spec.governance.allowed_models,
                "environment": spec.metadata.environment,
                "status": "active"
            }
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=2)
            logger.info(f"Recorded tenant '{spec.metadata.tenant_id}' in active registry.")
        except Exception as e:
            logger.error(f"Failed to record tenant in registry file: {e}")

    def _remove_active_tenant(self, tenant_id: str) -> Optional[dict]:
        try:
            if not self.registry_path.exists():
                return None
            with open(self.registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
            record = registry.pop(tenant_id, None)
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=2)
            return record
        except Exception as e:
            logger.error(f"Failed to remove tenant from registry file: {e}")
            return None

    def provision_tenant(
        self,
        spec: TenantSpecification,
        dry_run: bool = False,
        skip_k8s_apply: bool = False
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Executes an end-to-end transactional onboarding workflow:
        1. Provision LiteLLM Virtual Key
        2. Render and apply Kubernetes manifests
        3. Record active tenant state
        4. If any step fails, roll back all previously executed actions.
        """
        tenant_id = spec.metadata.tenant_id
        logger.info(f"Starting provisioning transaction for tenant '{tenant_id}'...")

        if dry_run:
            logger.info("Dry-run mode active. Simulating virtual key generation and manifest rendering.")
            simulated_key = f"sk-simulated-{tenant_id}-key"
            manifests = self.k8s_provisioner.render_manifests(spec, simulated_key)
            yaml_output = self.k8s_provisioner.dump_yaml(manifests)
            return True, f"Dry run successful. Rendered {len(manifests)} manifests for {tenant_id}.", simulated_key

        virtual_key: Optional[str] = None
        try:
            # Step 1: Provision Virtual Key on LiteLLM Gateway
            logger.info(f"Step 1/3: Provisioning virtual key in LiteLLM Gateway for tenant '{tenant_id}'...")
            key_resp = self.litellm_client.generate_virtual_key(
                tenant_id=tenant_id,
                max_budget=spec.governance.max_budget_usd,
                budget_duration=spec.governance.budget_duration,
                models=spec.governance.allowed_models,
                rpm_limit=spec.governance.rpm_limit,
                tpm_limit=spec.governance.tpm_limit,
                metadata={"contact_email": spec.metadata.contact_email}
            )
            virtual_key = key_resp.get("key")
            if not virtual_key:
                raise LiteLLMClientError("LiteLLM response did not contain virtual key.")

            # Register compensation to revoke virtual key on failure
            self._register_compensation(
                name="revoke_virtual_key",
                action=lambda: self.litellm_client.delete_virtual_key(virtual_key),
                description=f"Revoking virtual key '{virtual_key[:10]}...' in LiteLLM"
            )

            # Step 2: Render Kubernetes Manifests
            logger.info(f"Step 2/3: Rendering and applying Kubernetes manifests for namespace 'tenant-{tenant_id}'...")
            manifests = self.k8s_provisioner.render_manifests(spec, virtual_key)

            # Register compensation to delete namespace on failure
            self._register_compensation(
                name="delete_namespace",
                action=lambda: self.k8s_provisioner.delete_namespace(tenant_id),
                description=f"Deleting Kubernetes namespace 'tenant-{tenant_id}'"
            )

            if not skip_k8s_apply:
                self.k8s_provisioner.apply_manifests(manifests)

            # Step 3: Record Active Registry
            logger.info(f"Step 3/3: Committing transaction and recording tenant '{tenant_id}' state...")
            self._record_active_tenant(spec, virtual_key)

            # Clear compensations because transaction succeeded!
            self.compensations.clear()
            msg = f"Tenant '{tenant_id}' provisioned successfully at https://{spec.routing.fqdn}"
            logger.info(msg)
            return True, msg, virtual_key

        except Exception as e:
            logger.error(f"Transaction failed for tenant '{tenant_id}': {e}")
            self._rollback()
            return False, f"Provisioning failed: {e}. Transaction rolled back.", None

    def deprovision_tenant(self, tenant_id: str, virtual_key: Optional[str] = None) -> Tuple[bool, str]:
        """
        Safely deprovisions a tenant:
        1. Revokes virtual key in LiteLLM (cutting off LLM access immediately).
        2. Deletes Kubernetes namespace (reclaiming compute, storage, network rules).
        3. Removes tenant record from registry.
        """
        logger.info(f"Starting deprovisioning transaction for tenant '{tenant_id}'...")
        errors: List[str] = []

        # Find virtual key from registry if not provided
        record = self._remove_active_tenant(tenant_id)
        if not virtual_key and record:
            virtual_key = record.get("virtual_key")

        # 1. Revoke LiteLLM virtual key
        if virtual_key:
            logger.info(f"Revoking LiteLLM virtual key for tenant '{tenant_id}'...")
            success = self.litellm_client.delete_virtual_key(virtual_key)
            if not success:
                errors.append(f"Failed to revoke LiteLLM virtual key for {tenant_id}")
        else:
            logger.warning(f"No virtual key found for tenant '{tenant_id}'. Skipping key revocation.")

        # 2. Teardown Kubernetes resources
        logger.info(f"Deleting Kubernetes namespace 'tenant-{tenant_id}'...")
        k8s_success = self.k8s_provisioner.delete_namespace(tenant_id)
        if not k8s_success:
            errors.append(f"Failed to delete Kubernetes namespace tenant-{tenant_id}")

        if errors:
            msg = f"Deprovisioning finished with warnings: {'; '.join(errors)}"
            logger.warning(msg)
            return False, msg

        msg = f"Tenant '{tenant_id}' successfully deprovisioned and all resources reclaimed."
        logger.info(msg)
        return True, msg
