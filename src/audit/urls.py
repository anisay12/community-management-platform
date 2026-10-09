from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("", views.event_list, name="event_list"),
    path("<int:pk>/", views.event_detail, name="event_detail"),
]
