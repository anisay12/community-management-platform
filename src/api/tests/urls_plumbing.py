from django.urls import include, path
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import PagePagination


class NumbersView(ListAPIView):
    pagination_class = PagePagination

    def get_queryset(self):
        return list(range(120))

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(page)


class EchoView(APIView):
    def get(self, request):
        return Response({"ok": True})

    def post(self, request):
        return Response({"ok": True}, status=201)


urlpatterns = [
    path("api/t/numbers/", NumbersView.as_view()),
    path("api/t/echo/", EchoView.as_view()),
    path("", include("config.urls")),
]
