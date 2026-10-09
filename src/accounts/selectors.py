from django.db.models import Q, QuerySet

from .models import User


def users_for_admin(*, query: str = "", status: str = "") -> QuerySet[User]:
    """Users for the administration list, filtered by free text and status."""
    users = (
        User.objects.select_related("employment__unit")
        .prefetch_related("groups")
        .order_by("last_name", "first_name", "pk")
    )
    query = query.strip()
    if query:
        users = users.filter(
            Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    if status in User.Status.values:
        users = users.filter(status=status)
    return users
