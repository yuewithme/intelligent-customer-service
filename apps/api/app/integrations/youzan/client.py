from typing import Any

import httpx


class YouzanError(RuntimeError):
    """Raised when the Youzan Open API rejects or cannot serve a request."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        trace_id: str = "",
        method: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.trace_id = trace_id
        self.method = method


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
        try:
            if self.http_client is not None:
                response = await self.http_client.post(url, params=query, json=params)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, params=query, json=params)
        except httpx.RequestError as exc:
            raise YouzanError(
                "Youzan request failed",
                method=method,
            ) from exc
        if not 200 <= response.status_code < 300:
            raise YouzanError(
                f"Youzan returned HTTP {response.status_code}",
                code=str(response.status_code),
                method=method,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise YouzanError(
                "Youzan returned invalid JSON",
                method=method,
            ) from exc
        if not isinstance(payload, dict):
            raise YouzanError("Youzan returned an invalid response", method=method)
        self._raise_for_error(payload, method=method)
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        legacy_response = payload.get("response")
        return legacy_response if isinstance(legacy_response, dict) else payload

    @staticmethod
    def _raise_for_error(payload: dict[str, Any], *, method: str) -> None:
        for key in ("gw_err_resp", "error_response"):
            error = payload.get(key)
            if not isinstance(error, dict):
                continue
            code = str(
                error.get("err_code")
                or error.get("code")
                or error.get("error_code")
                or ""
            )
            trace_id = str(error.get("trace_id") or payload.get("trace_id") or "")
            message = str(
                error.get("err_msg")
                or error.get("msg")
                or error.get("message")
                or "Youzan request failed"
            )
            raise YouzanError(
                message,
                code=code,
                trace_id=trace_id,
                method=method,
            )

        raw_code = payload.get("code")
        failed = payload.get("success") is False
        if raw_code not in (None, ""):
            failed = failed or str(raw_code) != "200"
        if failed:
            raise YouzanError(
                str(payload.get("message") or "Youzan request failed"),
                code=str(raw_code or ""),
                trace_id=str(payload.get("trace_id") or ""),
                method=method,
            )
