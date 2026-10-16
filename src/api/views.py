"""Plumbing endpoints of the API: who am I, tag suggestions."""

from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from taxonomy.models import Tag
from taxonomy.services import normalize_tag_key

from .serializers import MeSerializer, TagSerializer


class MeView(APIView):
    """The signed-in user: identity, global roles, MFA requirement, preferences."""

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user, context={"request": request}).data)


TAG_SUGGESTIONS = 8


class TagListView(ListAPIView):
    """Tag suggestions: names whose key starts with ``q`` (at least 2 characters)."""

    serializer_class = TagSerializer
    pagination_class = None

    def get_queryset(self):
        query = self.request.query_params.get("q", "").strip()
        if len(query) < 2:
            return Tag.objects.none()
        return Tag.objects.filter(key__startswith=normalize_tag_key(query)).order_by("key")[
            :TAG_SUGGESTIONS
        ]
