from typing import Any

import httpx


class YouzanError(RuntimeError):
    """Raised when the Youzan Open API rejects or cannot serve a request."""


class YouzanClient:
    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = "https://open.youzanyun.com",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 15,
    ) -> None:
        self.access_token = access_token.strip()
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client
        self.timeout = timeout

    async def call(
        self,
        method: str,
        version: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.access_token:
            raise YouzanError("Youzan access token is not configured")
        url = f"{self.base_url}/api/{method}/{version}"
        query = {"access_token": self.access_token}
        if self.http_client is not None:
            response = await self.http_client.post(url, params=query, json=params)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, params=query, json=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise YouzanError("Youzan returned an invalid response")
        if payload.get("success") is False or str(payload.get("code")) not in {"", "200", "None"}:
            raise YouzanError(str(payload.get("message") or "Youzan request failed"))
        data = payload.get("data")
        return data if isinstance(data, dict) else payload
