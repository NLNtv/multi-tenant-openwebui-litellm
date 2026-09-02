"""
Tenant Manager CLI
Unified command-line interface for multi-tenant OpenWebUI and LiteLLM platform governance.
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from pathlib import Path

from tenant_manager.k8s_provisioner import K8sProvisioner
from tenant_manager.litellm_client import LiteLLMClient
from tenant_manager.models import TenantSpecification
from tenant_manager.transaction import ProvisioningTransactionManager
from tenant_manager.validator import SpecValidator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tenant-manager")


def get_default_litellm_url() -> str:
    return os.environ.get("LITELLM_URL", "http://localhost:4000")


def get_default_master_key() -> str:
    return os.environ.get("LITELLM_MASTER_KEY", "sk-litellm-master-super-secret-key-change-in-prod")


def cmd_validate(args: argparse.Namespace) -> int:
    result = SpecValidator.validate_file(args.spec_file)
    if result.is_valid:
        print(f"\n[SUCCESS] Configuration '{args.spec_file}' is valid!")
        print(f"  Tenant ID:   {result.spec.metadata.tenant_id}")
        print(f"  Name:        {result.spec.metadata.tenant_name}")
        print(f"  FQDN:        https://{result.spec.routing.fqdn}")
        print(f"  Max Budget:  ${result.spec.governance.max_budget_usd:.2f} / {result.spec.governance.budget_duration}")
        print(f"  Models:      {', '.join(result.spec.governance.allowed_models)}")
        print(f"  LDAP Host:   {result.spec.identity.server_host}:{result.spec.identity.server_port}")
        for w in result.warnings:
            print(f"  [WARNING]: {w}")
        return 0
    else:
        print(f"\n[FAILED] Configuration '{args.spec_file}' failed validation:")
        for err in result.errors:
            print(f"  - {err}")
        return 1


def cmd_render(args: argparse.Namespace) -> int:
    result = SpecValidator.validate_file(args.spec_file)
    if not result.is_valid:
        print(f"[FAILED] Cannot render invalid configuration: {result.errors}")
        return 1

    virtual_key = args.virtual_key or f"sk-simulated-{result.spec.metadata.tenant_id}"
    provisioner = K8sProvisioner(gateway_endpoint=args.gateway_endpoint)
    manifests = provisioner.render_manifests(result.spec, virtual_key)
    yaml_content = provisioner.dump_yaml(manifests)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        print(f"[SUCCESS] Wrote {len(manifests)} Kubernetes manifests to {out_path}")
    else:
        print(yaml_content)
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    result = SpecValidator.validate_file(args.spec_file)
    if not result.is_valid:
        print(f"[FAILED] Cannot provision: configuration validation failed:")
        for err in result.errors:
            print(f"  - {err}")
        return 1

    spec = result.spec
    litellm_client = LiteLLMClient(base_url=args.litellm_url, master_key=args.master_key)
    provisioner = K8sProvisioner(gateway_endpoint=args.gateway_endpoint)
    tx_manager = ProvisioningTransactionManager(litellm_client, provisioner)

    success, msg, vkey = tx_manager.provision_tenant(
        spec=spec,
        dry_run=args.dry_run,
        skip_k8s_apply=args.skip_k8s_apply
    )

    if success:
        print(f"\n[SUCCESS] {msg}")
        if vkey:
            print(f"  Virtual Key: {vkey}")
        print(f"  Subdomain:   https://{spec.routing.fqdn}")
        return 0
    else:
        print(f"\n[FAILED] {msg}")
        return 1


def cmd_deprovision(args: argparse.Namespace) -> int:
    litellm_client = LiteLLMClient(base_url=args.litellm_url, master_key=args.master_key)
    provisioner = K8sProvisioner()
    tx_manager = ProvisioningTransactionManager(litellm_client, provisioner)

    success, msg = tx_manager.deprovision_tenant(
        tenant_id=args.tenant_id,
        virtual_key=args.virtual_key
    )

    if success:
        print(f"\n[SUCCESS] {msg}")
        return 0
    else:
        print(f"\n[FAILED] {msg}")
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    registry_path = Path("tenants/active/registry.json")
    if not registry_path.exists():
        print("No active tenants found. Registry file does not exist.")
        return 0

    with open(registry_path, "r", encoding="utf-8") as f:
        try:
            registry = json.load(f)
        except json.JSONDecodeError:
            print("Registry file is empty or corrupted.")
            return 1

    if not registry:
        print("No active tenants registered.")
        return 0

    print(f"\n{'TENANT ID':<20} {'NAME':<25} {'FQDN':<30} {'BUDGET':<12} {'STATUS':<10}")
    print("-" * 100)
    for tid, info in registry.items():
        budget_str = f"${info.get('max_budget_usd', 0):.2f}/{info.get('budget_duration', '30d')}"
        print(f"{tid:<20} {info.get('tenant_name', ''):<25} {info.get('fqdn', ''):<30} {budget_str:<12} {info.get('status', 'unknown'):<10}")
    print("-" * 100)
    print(f"Total Active Tenants: {len(registry)}\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    registry_path = Path("tenants/active/registry.json")
    if not registry_path.exists():
        print(f"[ERROR] Registry not found.")
        return 1

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    info = registry.get(args.tenant_id)
    if not info:
        print(f"[ERROR] Tenant '{args.tenant_id}' not found in registry.")
        return 1

    print(f"\nTenant Status: {args.tenant_id}")
    print(f"  Name:        {info.get('tenant_name')}")
    print(f"  FQDN:        https://{info.get('fqdn')}")
    print(f"  Max Budget:  ${info.get('max_budget_usd', 0):.2f}")
    print(f"  Status:      {info.get('status')}")

    vkey = info.get("virtual_key")
    if vkey and not args.offline:
        try:
            litellm_client = LiteLLMClient(base_url=args.litellm_url, master_key=args.master_key)
            key_info = litellm_client.get_key_info(vkey)
            spend = key_info.get("info", {}).get("spend", 0.0)
            max_b = key_info.get("info", {}).get("max_budget", info.get("max_budget_usd", 0.0))
            print(f"  Live Spend:  ${spend:.4f} / ${max_b:.2f}")
            print(f"  Remaining:   ${max(0.0, max_b - spend):.4f}")
        except Exception as e:
            print(f"  Live Gateway Query: Unable to reach LiteLLM: {e}")

    return 0


def cmd_update_budget(args: argparse.Namespace) -> int:
    registry_path = Path("tenants/active/registry.json")
    if not registry_path.exists():
        print(f"[ERROR] Registry not found.")
        return 1

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    info = registry.get(args.tenant_id)
    if not info:
        print(f"[ERROR] Tenant '{args.tenant_id}' not found in registry.")
        return 1

    vkey = info.get("virtual_key")
    if not vkey:
        print(f"[ERROR] No virtual key recorded for tenant '{args.tenant_id}'.")
        return 1

    litellm_client = LiteLLMClient(base_url=args.litellm_url, master_key=args.master_key)
    try:
        updated = litellm_client.update_virtual_key(
            key=vkey,
            max_budget=args.budget,
            rpm_limit=args.rpm,
            tpm_limit=args.tpm
        )
        info["max_budget_usd"] = args.budget
        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)
        print(f"[SUCCESS] Updated budget for tenant '{args.tenant_id}' to ${args.budget:.2f}")
        return 0
    except Exception as e:
        print(f"[FAILED] Failed to update budget: {e}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tenant-manager",
        description="Enterprise Multi-Tenant Orchestration Engine for OpenWebUI and LiteLLM"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    val_parser = subparsers.add_parser("validate", help="Validate a tenant specification YAML file")
    val_parser.add_argument("spec_file", help="Path to tenant YAML specification")

    # render
    render_parser = subparsers.add_parser("render", help="Render Kubernetes manifests for a tenant")
    render_parser.add_argument("spec_file", help="Path to tenant YAML specification")
    render_parser.add_argument("-o", "--output", help="Output file path (default stdout)")
    render_parser.add_argument("-k", "--virtual-key", help="Virtual key to inject")
    render_parser.add_argument("--gateway-endpoint", default="http://litellm.litellm.svc.cluster.local:4000/v1")

    # provision
    prov_parser = subparsers.add_parser("provision", help="Onboard a new tenant end-to-end with atomic rollback")
    prov_parser.add_argument("spec_file", help="Path to tenant YAML specification")
    prov_parser.add_argument("--dry-run", action="store_true", help="Simulate provisioning without applying changes")
    prov_parser.add_argument("--skip-k8s-apply", action="store_true", help="Provision virtual key but skip kubectl apply")
    prov_parser.add_argument("--litellm-url", default=get_default_litellm_url(), help="LiteLLM Gateway Admin URL")
    prov_parser.add_argument("--master-key", default=get_default_master_key(), help="LiteLLM Master Key")
    prov_parser.add_argument("--gateway-endpoint", default="http://litellm.litellm.svc.cluster.local:4000/v1")

    # deprovision
    deprov_parser = subparsers.add_parser("deprovision", help="Teardown tenant workspace and revoke virtual key")
    deprov_parser.add_argument("tenant_id", help="Tenant ID to deprovision")
    deprov_parser.add_argument("-k", "--virtual-key", help="Optional virtual key to revoke")
    deprov_parser.add_argument("--litellm-url", default=get_default_litellm_url(), help="LiteLLM Gateway Admin URL")
    deprov_parser.add_argument("--master-key", default=get_default_master_key(), help="LiteLLM Master Key")

    # list
    subparsers.add_parser("list", help="List all registered active tenants")

    # status
    status_parser = subparsers.add_parser("status", help="Inspect tenant health and spend")
    status_parser.add_argument("tenant_id", help="Tenant ID to inspect")
    status_parser.add_argument("--offline", action="store_true", help="Skip querying live LiteLLM gateway")
    status_parser.add_argument("--litellm-url", default=get_default_litellm_url(), help="LiteLLM Gateway Admin URL")
    status_parser.add_argument("--master-key", default=get_default_master_key(), help="LiteLLM Master Key")

    # update-budget
    budget_parser = subparsers.add_parser("update-budget", help="Dynamically update tenant budget and rate limits")
    budget_parser.add_argument("tenant_id", help="Tenant ID to update")
    budget_parser.add_argument("--budget", type=float, required=True, help="New budget in USD")
    budget_parser.add_argument("--rpm", type=int, help="Optional new RPM limit")
    budget_parser.add_argument("--tpm", type=int, help="Optional new TPM limit")
    budget_parser.add_argument("--litellm-url", default=get_default_litellm_url(), help="LiteLLM Gateway Admin URL")
    budget_parser.add_argument("--master-key", default=get_default_master_key(), help="LiteLLM Master Key")

    args = parser.parse_args()
    command_map = {
        "validate": cmd_validate,
        "render": cmd_render,
        "provision": cmd_provision,
        "deprovision": cmd_deprovision,
        "list": cmd_list,
        "status": cmd_status,
        "update-budget": cmd_update_budget,
    }

    exit_code = command_map[args.command](args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
