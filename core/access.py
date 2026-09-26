"""Who may see what. One place, asked by the tile view, the GeoJSON API and
the upload view alike, so the rules cannot drift between them.

- public:  everyone.
- gated:   members of data_users (and staff/superusers).
- private: the owner, and for alignments the project's members.
"""
from django.db.models import Q

from .models import Visibility


def is_data_user(user):
    return user.is_authenticated and (
        user.is_superuser or user.is_staff or user.groups.filter(name="data_users").exists()
    )


def is_trail_editor(user):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name="trail_editors").exists()
    )


def visible_q(user, owner_field="owner", prefix=""):
    """A Q filter selecting the rows `user` may see, for a model with
    `visibility` and `owner` fields (optionally reached through `prefix`,
    e.g. "project__" for Alignment)."""
    q = Q(**{f"{prefix}visibility": Visibility.PUBLIC})
    if not user.is_authenticated:
        return q
    if is_data_user(user):
        q |= Q(**{f"{prefix}visibility": Visibility.GATED})
    q |= Q(**{f"{prefix}{owner_field}": user})
    if prefix == "project__":
        q |= Q(project__members=user)
    return q


def visibility_sql(user, table_alias):
    """The same rule as `visible_q`, as a SQL fragment for the MVT query.
    Returns (sql, params)."""
    sql = f"{table_alias}.visibility = 'public'"
    params = []
    if user.is_authenticated:
        if is_data_user(user):
            sql += f" OR {table_alias}.visibility = 'gated'"
        sql += f" OR {table_alias}.owner_id = %s"
        params.append(user.pk)
    return f"({sql})", params
