"""
LiteLLM Gateway API Client
Manages virtual keys, hard budgets, rate limits, and spend tracking via LiteLLM Admin REST API.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("tenant_manager.litellm")


class LiteLLMClientError(Exception):
    """Base exception for LiteLLM Gateway interactions."""
    pass


class LiteLLMClient:
    def __init__(self, base_url: str, master_key: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.master_key = master_key
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {self.master_key}",
            "Content-Type": "application/json"
        }

    def generate_virtual_key(
        self,
        tenant_id: str,
        max_budget: float,
        budget_duration: str,
        models: List[str],
        rpm_limit: int,
        tpm_limit: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Provisions a new isolated virtual key in LiteLLM for a corporate tenant.
        """
        endpoint = f"{self.base_url}/key/generate"
        payload = {
            "models": models,
            "max_budget": max_budget,
            "budget_duration": budget_duration,
            "rpm_limit": rpm_limit,
            "tpm_limit": tpm_limit,
            "key_alias": f"tenant-{tenant_id}",
            "metadata": {
                "tenant_id": tenant_id,
                **(metadata or {})
            }
        }

        try:
            response = requests.post(
                endpoint,
                headers=self.headers,
                json=payload,
                timeout=self.timeout
            )
            if response.status_code not in (200, 201):
                raise LiteLLMClientError(
                    f"Failed to generate virtual key for tenant '{tenant_id}': "
                    f"Status {response.status_code} - {response.text}"
                )
            data = response.json()
            key = data.get("key")
            if not key:
                raise LiteLLMClientError(
                    f"LiteLLM response did not return a valid virtual key: {data}"
                )
            logger.info(f"Successfully generated virtual key for tenant '{tenant_id}' with budget ${max_budget:.2f}")
            return data
        except requests.RequestException as e:
            raise LiteLLMClientError(f"Network error communicating with LiteLLM Gateway: {e}") from e

    def delete_virtual_key(self, key: str) -> bool:
        """
        Revokes and permanently removes a virtual key.
        """
        endpoint = f"{self.base_url}/key/delete"
        payload = {"keys": [key]}

        try:
            response = requests.post(
                endpoint,
                headers=self.headers,
                json=payload,
                timeout=self.timeout
            )
            if response.status_code not in (200, 204):
                logger.warning(
                    f"Virtual key deletion returned unexpected status {response.status_code}: {response.text}"
                )
                return False
            logger.info(f"Successfully revoked virtual key '{key[:10]}...'")
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to revoke key '{key[:10]}...': {e}")
            return False

    def get_key_info(self, key: str) -> Dict[str, Any]:
        """
        Fetches current budget, spend, and limits for a specific virtual key.
        """
        endpoint = f"{self.base_url}/key/info"
        try:
            response = requests.get(
                endpoint,
                headers=self.headers,
                params={"key": key},
                timeout=self.timeout
            )
            if response.status_code != 200:
                raise LiteLLMClientError(
                    f"Failed to fetch key info: Status {response.status_code} - {response.text}"
                )
            return response.json()
        except requests.RequestException as e:
            raise LiteLLMClientError(f"Error fetching key info from LiteLLM: {e}") from e

    def update_virtual_key(
        self,
        key: str,
        max_budget: Optional[float] = None,
        rpm_limit: Optional[int] = None,
        tpm_limit: Optional[int] = None,
        models: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Dynamically updates budget, limits, or whitelisted models for an active virtual key.
        """
        endpoint = f"{self.base_url}/key/update"
        payload: Dict[str, Any] = {"key": key}
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if rpm_limit is not None:
            payload["rpm_limit"] = rpm_limit
        if tpm_limit is not None:
            payload["tpm_limit"] = tpm_limit
        if models is not None:
            payload["models"] = models

        try:
            response = requests.post(
                endpoint,
                headers=self.headers,
                json=payload,
                timeout=self.timeout
            )
            if response.status_code != 200:
                raise LiteLLMClientError(
                    f"Failed to update virtual key: Status {response.status_code} - {response.text}"
                )
            return response.json()
        except requests.RequestException as e:
            raise LiteLLMClientError(f"Error updating key on LiteLLM: {e}") from e
