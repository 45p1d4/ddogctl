from __future__ import annotations

import json as _json
from typing import Any, Dict, Optional, Tuple

import requests

from .config import resolve_context


class ApiError(RuntimeError):
    def __init__(self, status_code: int, payload: Any):
        message = f"HTTP {status_code}: {payload}"
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class ApiClient:
    def __init__(self, site: str, api_key: Optional[str], app_key: Optional[str]):
        self.site = site
        self.api_key = api_key
        self.app_key = app_key
        self.base_url = f"https://api.{site}"
        self._timeout = 30

    @staticmethod
    def create_from_context(
        context_name: Optional[str],
        config_path: Optional[str],
    ) -> "ApiClient":
        site, api_key, app_key = resolve_context(context_name, config_path)
        return ApiClient(site=site, api_key=api_key, app_key=app_key)

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["DD-API-KEY"] = self.api_key
        if self.app_key:
            headers["DD-APPLICATION-KEY"] = self.app_key
        return headers

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        resp = requests.request(
            method=method.upper(),
            url=url,
            headers=self._headers(),
            params=params,
            json=json,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            payload = None
            try:
                payload = resp.json()
            except Exception:
                payload = resp.text
            raise ApiError(resp.status_code, payload)
        # Prefer JSON; some Datadog endpoints may not set content-type strictly.
        try:
            return resp.json()
        except Exception:
            return resp.text

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("POST", path, json=json)

