"""Bounded, validated extraction from a paginated HR HTTP source."""
import time
from typing import Any

import httpx

from .rules import valid_employee


class ExtractionError(RuntimeError):
    """Safe error with no response payload or credential-bearing URL."""


def fetch_employees(url: str, *, client: httpx.Client | None = None, token: str | None = None, max_pages: int = 100, retries: int = 2) -> list[dict[str, Any]]:
    """Extract all pages or fail without returning a partial employee snapshot."""
    if max_pages < 1 or not 0 <= retries <= 5:
        raise ValueError('Invalid extraction bounds')
    owned_client = client is None
    http = client or httpx.Client(timeout=10.0, follow_redirects=False)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    try:
        for page in range(1, max_pages + 1):
            response = None
            for attempt in range(retries + 1):
                try:
                    response = http.get(url, params={'page': page, 'page_size': 25}, headers=headers, timeout=10.0)
                    if response.status_code in (429, 500, 502, 503, 504):
                        if attempt == retries:
                            raise ExtractionError('HR source unavailable after bounded retries')
                    else:
                        response.raise_for_status()
                        break
                except httpx.TransportError:
                    if attempt == retries:
                        raise ExtractionError('HR source unreachable after bounded retries') from None
                except httpx.HTTPStatusError:
                    raise ExtractionError('HR source rejected the extraction request') from None
                time.sleep(0.05 * (2 ** attempt))
            try:
                body = response.json()
            except (ValueError, AttributeError):
                raise ExtractionError('HR source returned invalid JSON') from None
            if not isinstance(body, dict) or not isinstance(body.get('items'), list) or type(body.get('has_more')) is not bool:
                raise ExtractionError('HR source violated the page contract')
            if body['has_more'] and not body['items']:
                raise ExtractionError('HR source returned an empty nonterminal page')
            for item in body['items']:
                if not valid_employee(item):
                    raise ExtractionError('HR source violated the employee contract')
                identifier = item['employee_id']
                if identifier in seen:
                    raise ExtractionError('HR source contains invalid or repeated employee IDs')
                seen.add(identifier)
                rows.append(item)
            if not body['has_more']:
                return rows
        raise ExtractionError('HR pagination exceeded the configured limit')
    finally:
        if owned_client:
            http.close()
