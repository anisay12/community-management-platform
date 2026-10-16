import uuid

from django.urls import include, path
from rest_framework.response import Response
from rest_framework.views import APIView

from api.idempotency import Outcome, mark_replayed, run_idempotent

CALLS: list[str] = []


class CreateView(APIView):
    required = False

    def post(self, request):
        def operation():
            public_id = str(uuid.uuid4())
            CALLS.append(public_id)
            return Outcome(status=201, kind="thing", public_id=public_id)

        outcome = run_idempotent(request, "test:create", operation, required=self.required)
        return mark_replayed(Response({"id": outcome.public_id}, status=outcome.status), outcome)


class RequiredCreateView(CreateView):
    required = True


class FailingCreateView(APIView):
    def post(self, request):
        def operation():
            raise OSError("disk full")

        run_idempotent(request, "test:failing", operation)


urlpatterns = [
    path("api/t/create/", CreateView.as_view()),
    path("api/t/create-required/", RequiredCreateView.as_view()),
    path("api/t/create-failing/", FailingCreateView.as_view()),
    path("", include("config.urls")),
]
