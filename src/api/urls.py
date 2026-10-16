from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("v1/me/", views.MeView.as_view(), name="me"),
]
