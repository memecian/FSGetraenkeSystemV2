"""HTTP client for the nanposweb NFC API.

All methods raise :class:`APIError` on HTTP errors or network problems.
Callers should catch that exception and handle it gracefully.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Raised when the server returns an error or the request fails."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class NFCApiClient:
    """Thin wrapper around the ``/api/nfc/*`` endpoints."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 5.0) -> None:
        self._base = base_url.rstrip('/')
        self._session = requests.Session()
        self._session.headers['Authorization'] = f'Bearer {api_key}'
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, **params: Any) -> Any:
        url = f'{self._base}{path}'
        try:
            resp = self._session.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            raise APIError(f'Network error: {exc}') from exc

        if not resp.ok:
            msg = resp.json().get('error', resp.text) if resp.content else resp.reason
            raise APIError(msg, resp.status_code)

        return resp.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        url = f'{self._base}{path}'
        try:
            resp = self._session.post(url, json=body, timeout=self._timeout)
        except requests.RequestException as exc:
            raise APIError(f'Network error: {exc}') from exc

        if not resp.ok:
            msg = resp.json().get('error', resp.text) if resp.content else resp.reason
            raise APIError(msg, resp.status_code)

        return resp.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_products(self) -> list[dict[str, Any]]:
        """Return a list of visible products: ``[{id, name, price}, …]``.

        ``price`` is in cents.
        """
        return self._get('/api/nfc/products')  # type: ignore[return-value]

    def identify(self, card_uid: str) -> dict[str, Any]:
        """Look up the user for *card_uid*.

        Returns ``{user_id, name, is_admin, balance}`` where ``balance`` is in
        cents.  Raises :class:`APIError` with ``status_code == 404`` if the
        card is not registered.
        """
        return self._post('/api/nfc/identify', {'card_uid': card_uid})  # type: ignore[return-value]

    def purchase(self, card_uid: str, product_id: int) -> dict[str, Any]:
        """Purchase *product_id* for the user identified by *card_uid*.

        Returns ``{success, product_name, amount, new_balance}`` where monetary
        values are in cents.
        """
        return self._post('/api/nfc/purchase', {'card_uid': card_uid, 'product_id': product_id})  # type: ignore[return-value]

    def get_users(self, admin_card_uid: str) -> list[dict[str, Any]]:
        """Return all users with balances for the admin panel.

        Requires the admin's card UID to verify identity.
        Returns ``[{id, name, balance}, …]`` where ``balance`` is in cents.
        """
        return self._post('/api/nfc/users', {'admin_card_uid': admin_card_uid})  # type: ignore[return-value]

    def admin_balance(
        self,
        admin_card_uid: str,
        user_id: int,
        amount: float,
        recharge: bool = True,
    ) -> dict[str, Any]:
        """Adjust *user_id*'s balance.

        *amount* is in **euros**.  Set *recharge* to ``False`` to subtract.
        Returns ``{success, new_balance}`` where ``new_balance`` is in cents.
        """
        return self._post(
            '/api/nfc/admin/balance',
            {
                'admin_card_uid': admin_card_uid,
                'user_id': user_id,
                'amount': amount,
                'recharge': recharge,
            },
        )  # type: ignore[return-value]

    def get_debtors(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the top debtors (users with negative balance): ``[{id, name, balance, debt}, …]``.

        ``balance`` and ``debt`` are in cents.
        """
        return self._get('/api/nfc/debtors', limit=limit)  # type: ignore[return-value]

