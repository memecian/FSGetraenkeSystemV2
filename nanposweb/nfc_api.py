"""NFC API blueprint - used by Raspberry Pi NFC-reader clients.

Every endpoint is protected by a shared secret (``NFC_API_KEY`` in the server
config).  Set it to a long random string; without it all endpoints return 503.

Admin operations additionally require the scanned admin card UID to be
provided in the request body so the server can verify the identity without
storing any session state.
"""

import datetime
from collections.abc import Callable
from functools import wraps
from typing import Any, Union

from flask import Blueprint, current_app, jsonify, request
from werkzeug.wrappers import Response

from .db import db
from .db.helpers import get_balance
from .db.models import Product, Revenue, User
from .helpers import calc_hash

nfc_api_bp = Blueprint('nfc_api', __name__, url_prefix='/api/nfc')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AnyCallable = Callable[..., Any]


def _require_api_key(f: _AnyCallable) -> _AnyCallable:
    """Decorator: reject requests that don't carry the correct bearer token."""

    @wraps(f)
    def _decorated(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        api_key: str | None = current_app.config.get('NFC_API_KEY')
        if not api_key:
            return jsonify({'error': 'NFC API not configured on this server'}), 503

        auth_header = request.headers.get('Authorization', '')
        token = auth_header.removeprefix('Bearer ')
        if not auth_header.startswith('Bearer ') or token != api_key:
            return jsonify({'error': 'Unauthorized'}), 401

        return f(*args, **kwargs)

    return _decorated  # type: ignore[return-value]


def _get_admin_by_card(card_uid: str) -> Union[User, None]:
    """Return the User if *card_uid* belongs to a registered admin, else None."""
    card_hash = calc_hash(card_uid)
    return User.query.filter_by(card=card_hash, isop=True).one_or_none()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@nfc_api_bp.route('/products', methods=['GET'])
@_require_api_key
def products() -> Response:
    """Return all visible products as JSON.

    Response body::

        [{"id": 1, "name": "Club Mate", "price": 150}, …]

    ``price`` is in **cents**.
    """
    product_list = Product.query.filter_by(visible=True).order_by(Product.name).all()
    return jsonify([{'id': p.id, 'name': p.name, 'price': p.price} for p in product_list])


@nfc_api_bp.route('/identify', methods=['POST'])
@_require_api_key
def identify() -> Response:
    """Identify a user by NFC card UID.

    Request body::

        {"card_uid": "04AABBCC"}

    Response body (200)::

        {"user_id": 1, "name": "Alice", "is_admin": false, "balance": 1250}

    ``balance`` is in **cents**.  Returns 404 when the card is not registered.
    """
    data = request.get_json(silent=True) or {}
    if 'card_uid' not in data:
        return jsonify({'error': 'card_uid required'}), 400  # type: ignore[return-value]

    card_hash = calc_hash(str(data['card_uid']))
    user: User | None = User.query.filter_by(card=card_hash).one_or_none()

    if user is None:
        return jsonify({'error': 'Card not registered'}), 404  # type: ignore[return-value]

    most_buyed_timestamp = datetime.datetime.now() - datetime.timedelta(days=current_app.config.get('FAVORITES_DAYS', 100))
    most_buyed_query = (
        db.select(Product, db.func.count(Revenue.product).label('CTR'))
        .join(Product)
        .where(Revenue.product is not None)
        .where(Revenue.user == user.id)
        .where(Revenue.date >= most_buyed_timestamp)
        .where(Product.visible)
        .group_by(Product.id)
        .order_by(db.desc('CTR'))
    )
    most_buyed = db.session.execute(most_buyed_query).all()
    favorites = [f[0].id for f in most_buyed[: current_app.config.get('FAVORITES_DISPLAY', 3)]]

    return jsonify({
        'user_id': user.id,
        'name': user.name,
        'is_admin': user.isop,
        'balance': get_balance(user.id),
        'favorites': favorites,
    })


@nfc_api_bp.route('/purchase', methods=['POST'])
@_require_api_key
def purchase() -> Response:
    """Purchase a product for the user identified by their NFC card.

    Request body::

        {"card_uid": "04AABBCC", "product_id": 3}

    Response body (200)::

        {"success": true, "product_name": "Club Mate", "amount": 150,
         "new_balance": 1100}

    ``amount`` and ``new_balance`` are in **cents**.
    """
    data = request.get_json(silent=True) or {}
    if 'card_uid' not in data or 'product_id' not in data:
        return jsonify({'error': 'card_uid and product_id required'}), 400  # type: ignore[return-value]

    card_hash = calc_hash(str(data['card_uid']))
    user: User | None = User.query.filter_by(card=card_hash).one_or_none()
    if user is None:
        return jsonify({'error': 'Card not registered'}), 404  # type: ignore[return-value]

    product: Product | None = Product.query.filter_by(id=data['product_id'], visible=True).one_or_none()
    if product is None:
        return jsonify({'error': 'Product not found'}), 404  # type: ignore[return-value]

    rev = Revenue(user=user.id, product=product.id, amount=-product.price)
    db.session.add(rev)
    db.session.commit()

    return jsonify({
        'success': True,
        'product_name': product.name,
        'amount': product.price,
        'new_balance': get_balance(user.id),
    })


@nfc_api_bp.route('/users', methods=['POST'])
@_require_api_key
def users() -> Response:
    """List all users with their current balances.

    Requires an admin card UID so only admins can fetch this list.

    Request body::

        {"admin_card_uid": "04AABBCC"}

    Response body (200)::

        [{"id": 1, "name": "Alice", "balance": 1250}, …]

    ``balance`` is in **cents**.
    """
    data = request.get_json(silent=True) or {}
    if 'admin_card_uid' not in data:
        return jsonify({'error': 'admin_card_uid required'}), 400  # type: ignore[return-value]

    if _get_admin_by_card(str(data['admin_card_uid'])) is None:
        return jsonify({'error': 'Admin card not recognized'}), 403  # type: ignore[return-value]

    all_users = User.query.order_by(User.name).all()
    return jsonify([{'id': u.id, 'name': u.name, 'balance': get_balance(u.id)} for u in all_users])


@nfc_api_bp.route('/admin/balance', methods=['POST'])
@_require_api_key
def admin_balance() -> Response:
    """Adjust a user's balance.  Requires an admin card UID.

    Request body::

        {
            "admin_card_uid": "04AABBCC",
            "user_id": 2,
            "amount": 10.00,
            "recharge": true
        }

    * ``amount`` is in **euros** (float).
    * ``recharge``: ``true`` to add money, ``false`` to subtract it.

    Response body (200)::

        {"success": true, "new_balance": 2250}

    ``new_balance`` is in **cents**.
    """
    data = request.get_json(silent=True) or {}
    required_fields = ('admin_card_uid', 'user_id', 'amount')
    if not all(k in data for k in required_fields):
        return jsonify({'error': f'{", ".join(required_fields)} required'}), 400  # type: ignore[return-value]

    admin_user = _get_admin_by_card(str(data['admin_card_uid']))
    if admin_user is None:
        return jsonify({'error': 'Admin card not recognized'}), 403  # type: ignore[return-value]

    user: User | None = User.query.get(data['user_id'])
    if user is None:
        return jsonify({'error': 'User not found'}), 404  # type: ignore[return-value]

    amount_cents = int(float(data['amount']) * 100)
    factor = 1 if data.get('recharge', True) else -1

    rev = Revenue(user=user.id, product=None, amount=amount_cents * factor, admin_id=admin_user.id)
    db.session.add(rev)
    db.session.commit()

    return jsonify({'success': True, 'new_balance': get_balance(user.id)})


@nfc_api_bp.route('/debtors', methods=['GET'])
@nfc_api_bp.route('/most_wanted', methods=['GET'])
@_require_api_key
def debtors() -> Response:
    """Return top debtors (users with negative balance) ordered by most debt.

    Query parameters:
        limit (optional, default 10): maximum number of debtors to return.

    Response body::

        [{"id": 1, "name": "Alice", "balance": -2500, "debt": 2500}, …]

    ``balance`` and ``debt`` are in **cents**.
    """
    limit_param = request.args.get('limit', 10, type=int) or 10
    limit = max(1, min(limit_param, 100))

    aggregation = (
        db.select(
            Revenue.user.label('user_id'),
            db.func.sum(Revenue.amount).label('balance'),
        )
        .group_by(Revenue.user)
        .subquery()
    )

    user_query = (
        db.select(
            User.id,
            User.name,
            db.func.coalesce(aggregation.c.balance, 0).label('balance'),
        )
        .outerjoin(aggregation, User.id == aggregation.c.user_id)
        .filter(db.func.coalesce(aggregation.c.balance, 0) < 0)
        .order_by(db.asc('balance'), User.name.asc())
        .limit(limit)
    )

    debtors_list = db.session.execute(user_query).all()
    return jsonify([
        {
            'id': row.id,
            'name': row.name,
            'balance': int(row.balance),
            'debt': int(-row.balance),
        }
        for row in debtors_list
    ])

@nfc_api_bp.route('/rich_bitches', methods=['GET'])
@_require_api_key
def rich_bitches() -> Response:
    """Return top savers (users with positive balance) ordered by most balance.

    Query parameters:
        limit (optional, default 5): maximum number of users to return.

    Response body::

        [{"id": 1, "name": "Alice", "balance": 2500}, …]

    ``balance`` is in **cents**.
    """
    limit_param = request.args.get('limit', 5, type=int) or 5
    limit = max(1, min(limit_param, 100))

    aggregation = (
        db.select(
            Revenue.user.label('user_id'),
            db.func.sum(Revenue.amount).label('balance'),
        )
        .group_by(Revenue.user)
        .subquery()
    )

    user_query = (
        db.select(
            User.id,
            User.name,
            db.func.coalesce(aggregation.c.balance, 0).label('balance'),
        )
        .outerjoin(aggregation, User.id == aggregation.c.user_id)
        .filter(db.func.coalesce(aggregation.c.balance, 0) > 0)
        .order_by(db.desc('balance'), User.name.asc())
        .limit(limit)
    )

    savers_list = db.session.execute(user_query).all()
    return jsonify([
        {
            'id': row.id,
            'name': row.name,
            'balance': int(row.balance),
        }
        for row in savers_list
    ])
