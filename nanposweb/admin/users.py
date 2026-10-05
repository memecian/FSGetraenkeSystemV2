from typing import Union

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from werkzeug.wrappers import Response

from .forms import BalanceForm, UserForm
from .helpers import admin_permission
from ..db import db
from ..db.helpers import revenue_query
from ..db.models import Revenue, User
from ..helpers import calc_hash

users_bp = Blueprint('users', __name__, url_prefix='/users')


@users_bp.route('/')
@login_required
@admin_permission.require(http_exception=401)
def index() -> str:
    aggregation = (
        db.select(db.func.sum(Revenue.amount).label('balance'), Revenue.user.label('user_id')).group_by(Revenue.user).subquery()
    )
    user_query = (
        db.select(User, db.func.coalesce(aggregation.c.balance, 0))
        .outerjoin(aggregation, User.id == aggregation.c.user_id)
        .order_by(User.name)
    )
    users_list = db.session.execute(user_query).all()
    return render_template('users/index.html', users=users_list)


@users_bp.route('/', methods=['POST'])
@login_required
@admin_permission.require(http_exception=401)
def post() -> Union[Response, str]:
    form = UserForm()
    if not form.validate_on_submit():
        flash('Submitted form was not valid!', category='danger')
        return render_template('products/form.html', form=form, edit=True)

    create = False
    user = User.query.filter_by(id=form.id.data).one_or_none()
    if user is None:
        create = True
        user = User()

    new_card_hash = None
    if not form.unset_card.data and form.card.data:
        new_card_hash = calc_hash(form.card.data)
        card_query = User.query.filter(User.card == new_card_hash)
        if not create:
            card_query = card_query.filter(User.id != user.id)
        card_owner = card_query.first()
        if card_owner is not None:
            flash(f'This card is already registered to user "{card_owner.name}"', category='danger')
            return render_template('users/form.html', form=form, edit=not create)

    user.name = form.name.data
    user.isop = form.isop.data

    if form.unset_pin.data:
        user.pin = None
    elif form.pin.data:
        user.pin = calc_hash(form.pin.data)

    if form.unset_card.data:
        user.card = None
    elif new_card_hash is not None:
        user.card = new_card_hash

    if create:
        db.session.add(user)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('Could not save user: name or card is already registered to another user', category='danger')
        return render_template('users/form.html', form=form, edit=not create)

    if create:
        flash(f'Created user {form.name.data}', category='success')
    else:
        flash(f'Updated user "{form.name.data}"', category='success')

    return redirect(url_for('admin.users.index'))


@users_bp.route('/impersonate/<user_id>')
@login_required
@admin_permission.require(http_exception=401)
def impersonate(user_id: int) -> Response:
    session['impersonate'] = user_id
    return redirect(url_for('main.index'))


@users_bp.route('/impersonate/pop')
@login_required
@admin_permission.require(http_exception=401)
def pop_impersonate() -> Response:
    session.pop('impersonate', None)
    return redirect(url_for('admin.users.index'))


@users_bp.route('/balance/<user_id>', methods=['GET', 'POST'])
@login_required
@admin_permission.require(http_exception=401)
def balance(user_id: int) -> Union[Response, str]:
    user = User.query.get(user_id)
    form = BalanceForm()

    if request.method == 'POST':
        if form.validate_on_submit() and form.amount.data:
            euros = form.amount.data
            cents = int(euros * 100)

            if form.recharge.data:
                factor = 1
                flash(f'Added {euros:.2f} € for {user.name}', category='success')
            elif form.charge.data:
                factor = -1
                flash(f'Charged {euros:.2f} € from {user.name}', category='success')
            else:
                flash('Submitted form was not valid!', category='danger')
                return render_template('users/balance.html', form=form, user=user)

            rev = Revenue(user=user.id, product=None, amount=cents * factor, admin_id=current_user.id)
            db.session.add(rev)
            db.session.commit()
            return redirect(url_for('admin.users.index'))

        flash('Submitted form was not valid!', category='danger')

    return render_template('users/balance.html', form=form, user=user)


@users_bp.route('/revenues/<user_id>', methods=['GET'])
@login_required
@admin_permission.require(http_exception=401)
def revenues(user_id: int) -> str:
    user = User.query.get(user_id)
    revenues_query = revenue_query(user_id)

    return render_template('users/revenues.html', user=user, revenues=db.session.execute(revenues_query).all())


@users_bp.route('/add')
@login_required
@admin_permission.require(http_exception=401)
def add() -> str:
    form = UserForm()
    return render_template('users/form.html', form=form, edit=False)


@users_bp.route('/edit/<user_id>')
@login_required
@admin_permission.require(http_exception=401)
def edit(user_id: int) -> str:
    user = User.query.get(user_id)
    form = UserForm(id=user.id, name=user.name, isop=user.isop)

    return render_template('users/form.html', form=form, edit=True)


@users_bp.route('/delete/<user_id>')
@login_required
@admin_permission.require(http_exception=401)
def delete(user_id: int) -> Response:
    user = User.query.get(user_id)
    db.session.delete(user)
    db.session.commit()
    flash(f'Deleted user "{user.name}"', category='success')
    return redirect(url_for('admin.users.index'))
