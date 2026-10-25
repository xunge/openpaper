"""Create the paper bucket and the one policy MinIO needs beyond AWS defaults.

Run inside the server container (it already has boto3 and the S3 env vars):

    docker compose -f docker-compose.dev.yml exec -T server python - < docker/init-minio.py

Two things differ from the hosted deployment:

* The bucket has to exist. Upstream's bucket is provisioned outside the app.
* Preview images are served through a plain URL stored on the paper row
  (`papers.preview_url`), because upstream fronts the bucket with a public CDN
  and signs nothing. MinIO keeps buckets private, so without this policy every
  thumbnail 403s. Only the preview objects are opened up; PDFs and everything
  else keep going through short-lived presigned URLs.
"""

import json
import os

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

BUCKET = os.environ.get("S3_BUCKET_NAME", "openpaper")

client = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)

try:
    client.create_bucket(Bucket=BUCKET)
    print(f"bucket created: {BUCKET}")
except ClientError as exc:
    code = exc.response.get("Error", {}).get("Code", "")
    if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
        print(f"bucket already present: {BUCKET}")
    else:
        raise

policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublicPreviewImages",
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{BUCKET}/uploads/*preview*"],
        }
    ],
}
client.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(policy))
print("preview policy applied")

print("buckets:", [b["Name"] for b in client.list_buckets().get("Buckets", [])])
