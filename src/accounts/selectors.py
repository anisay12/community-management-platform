from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import DataExport, User


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


def current_data_export(user) -> DataExport | None:
    """The export that is pending or still available for download, if any.

    While there is one, the user cannot request another.
    """
    return (
        DataExport.objects.filter(user=user)
        .filter(
            Q(status=DataExport.Status.PENDING)
            | Q(status=DataExport.Status.READY, expires_at__gt=timezone.now())
        )
        .first()
    )
