from django.http import HttpResponse
from django.urls import include, path


def plain_view(request):
    return HttpResponse("plain")


urlpatterns = [
    path("api/plain/", plain_view, name="plain"),
    path("", include("config.urls")),
]
