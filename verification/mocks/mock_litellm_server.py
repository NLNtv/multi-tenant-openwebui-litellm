"""
Mock LiteLLM Proxy Server
Lightweight HTTP server simulating LiteLLM Gateway for testing virtual keys, spend tracking, and budget cutoffs.
"""

from __future__ import annotations
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse


class MockLiteLLMState:
    def __init__(self):
        self.lock = threading.Lock()
        self.master_key = "sk-litellm-master-super-secret-key-change-in-prod"
        # key_string -> dict of key metadata
        self.keys: Dict[str, Dict[str, Any]] = {}
        # spend per key
        self.spend: Dict[str, float] = {}

    def reset(self):
        with self.lock:
            self.keys.clear()
            self.spend.clear()


GLOBAL_STATE = MockLiteLLMState()


class MockLiteLLMHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: Any):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _get_bearer_token(self) -> Optional[str]:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/health", "/health/liveliness", "/health/readiness"):
            return self._send_json(200, {"status": "healthy"})

        if path == "/key/info":
            token = self._get_bearer_token()
            if token != GLOBAL_STATE.master_key:
                return self._send_json(401, {"error": "Unauthorized: Invalid Master Key"})

            params = parse_qs(parsed.query)
            key = params.get("key", [None])[0]
            if not key or key not in GLOBAL_STATE.keys:
                return self._send_json(404, {"error": f"Key '{key}' not found"})

            key_data = GLOBAL_STATE.keys[key]
            current_spend = GLOBAL_STATE.spend.get(key, 0.0)
            return self._send_json(200, {
                "key": key,
                "info": {
                    **key_data,
                    "spend": current_spend
                }
            })

        self._send_json(404, {"error": f"Endpoint {path} not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()

        # 1. /key/generate
        if path == "/key/generate":
            token = self._get_bearer_token()
            if token != GLOBAL_STATE.master_key:
                return self._send_json(401, {"error": "Unauthorized: Invalid Master Key"})

            virtual_key = f"sk-tenant-{secrets.token_hex(16)}"
            max_budget = float(body.get("max_budget", 100.0))
            budget_duration = body.get("budget_duration", "30d")
            models = body.get("models", ["gpt-4o"])
            rpm_limit = body.get("rpm_limit", 120)
            tpm_limit = body.get("tpm_limit", 100000)
            metadata = body.get("metadata", {})

            with GLOBAL_STATE.lock:
                GLOBAL_STATE.keys[virtual_key] = {
                    "max_budget": max_budget,
                    "budget_duration": budget_duration,
                    "models": models,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "metadata": metadata,
                    "key_alias": body.get("key_alias", "")
                }
                GLOBAL_STATE.spend[virtual_key] = 0.0

            return self._send_json(200, {
                "key": virtual_key,
                "max_budget": max_budget,
                "models": models,
                "status": "success"
            })

        # 2. /key/delete
        if path == "/key/delete":
            token = self._get_bearer_token()
            if token != GLOBAL_STATE.master_key:
                return self._send_json(401, {"error": "Unauthorized: Invalid Master Key"})

            keys_to_delete = body.get("keys", [])
            with GLOBAL_STATE.lock:
                for k in keys_to_delete:
                    GLOBAL_STATE.keys.pop(k, None)
                    GLOBAL_STATE.spend.pop(k, None)

            return self._send_json(200, {"status": "success", "deleted": keys_to_delete})

        # 3. /key/update
        if path == "/key/update":
            token = self._get_bearer_token()
            if token != GLOBAL_STATE.master_key:
                return self._send_json(401, {"error": "Unauthorized: Invalid Master Key"})

            key = body.get("key")
            if not key or key not in GLOBAL_STATE.keys:
                return self._send_json(404, {"error": "Key not found"})

            with GLOBAL_STATE.lock:
                if "max_budget" in body:
                    GLOBAL_STATE.keys[key]["max_budget"] = float(body["max_budget"])
                if "models" in body:
                    GLOBAL_STATE.keys[key]["models"] = body["models"]
                if "rpm_limit" in body:
                    GLOBAL_STATE.keys[key]["rpm_limit"] = body["rpm_limit"]
                if "tpm_limit" in body:
                    GLOBAL_STATE.keys[key]["tpm_limit"] = body["tpm_limit"]

            return self._send_json(200, {"status": "success", "key": key, "info": GLOBAL_STATE.keys[key]})

        # 4. /v1/chat/completions (Simulated Model Inference with Budget Enforcement)
        if path == "/v1/chat/completions":
            virtual_key = self._get_bearer_token()
            if not virtual_key or virtual_key not in GLOBAL_STATE.keys:
                return self._send_json(401, {
                    "error": {
                        "message": "Invalid virtual key or unauthorized",
                        "type": "authentication_error"
                    }
                })

            key_meta = GLOBAL_STATE.keys[virtual_key]
            requested_model = body.get("model", "gpt-4o")

            # Validate Model Whitelist
            if requested_model not in key_meta["models"]:
                return self._send_json(400, {
                    "error": {
                        "message": f"Model '{requested_model}' is not permitted under tenant key permissions. Allowed: {key_meta['models']}",
                        "type": "permission_denied"
                    }
                })

            # Check Hard Budget Cutoff
            current_spend = GLOBAL_STATE.spend.get(virtual_key, 0.0)
            max_budget = key_meta["max_budget"]
            simulated_cost = 0.05  # $0.05 per request for testing

            if current_spend >= max_budget:
                return self._send_json(429, {
                    "error": {
                        "message": f"BudgetExceededError: Key max budget of ${max_budget:.2f} has been reached. Current spend: ${current_spend:.2f}. Request rejected.",
                        "type": "budget_exceeded_error",
                        "code": "budget_exceeded"
                    }
                })

            # Increment spend
            with GLOBAL_STATE.lock:
                GLOBAL_STATE.spend[virtual_key] = current_spend + simulated_cost

            return self._send_json(200, {
                "id": f"chatcmpl-{secrets.token_hex(8)}",
                "object": "chat.completion",
                "model": requested_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"Hello! This is an enterprise-isolated AI response from {requested_model}."
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": 25,
                    "completion_tokens": 15,
                    "total_tokens": 40
                }
            })

        self._send_json(404, {"error": f"Endpoint {path} not found"})

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging during tests
        return


class MockLiteLLMServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 14000):
        self.host = host
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        GLOBAL_STATE.reset()
        self.server = HTTPServer((self.host, self.port), MockLiteLLMHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"
