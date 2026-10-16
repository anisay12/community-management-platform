"""Numbered pagination for catalogues and lists (spec § 2: size 20, max 50)."""

from rest_framework.pagination import PageNumberPagination


class PagePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50
