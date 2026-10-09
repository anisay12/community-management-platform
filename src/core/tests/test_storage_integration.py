import os
import urllib.error
import urllib.request
import uuid

import boto3
import pytest
from django.core.files.base import ContentFile
from storages.backends.s3 import S3Storage

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("S3_INTEGRATION_ENDPOINT"),
        reason="nécessite un stockage S3-compatible (exécuté en CI et via `make test-integration`)",
    ),
]


@pytest.fixture
def private_storage() -> S3Storage:
    endpoint = os.environ["S3_INTEGRATION_ENDPOINT"]
    bucket = os.environ["S3_INTEGRATION_BUCKET"]
    access_key = os.environ["S3_INTEGRATION_ACCESS_KEY"]
    secret_key = os.environ["S3_INTEGRATION_SECRET_KEY"]
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if bucket not in existing:
        client.create_bucket(Bucket=bucket)
    return S3Storage(
        bucket_name=bucket,
        endpoint_url=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        region_name="us-east-1",
        default_acl=None,
        querystring_auth=True,
        file_overwrite=False,
    )


def test_private_bucket_roundtrip_and_no_anonymous_read(private_storage):
    endpoint = os.environ["S3_INTEGRATION_ENDPOINT"]
    bucket = os.environ["S3_INTEGRATION_BUCKET"]
    name = private_storage.save(f"l0-check/{uuid.uuid4().hex}.txt", ContentFile(b"bonjour"))
    try:
        with private_storage.open(name) as fh:
            assert fh.read() == b"bonjour"
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{endpoint}/{bucket}/{name}", timeout=5)
        assert exc.value.code == 403
    finally:
        private_storage.delete(name)
