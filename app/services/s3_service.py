import boto3
from botocore.exceptions import ClientError
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class S3Service:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        self.bucket_name = settings.AWS_S3_BUCKET_NAME

    def generate_presigned_upload_url(self, object_key: str, expiration=3600):
        """Generate a presigned URL for the client to upload a file directly to S3."""
        if not self.bucket_name:
            raise ValueError("AWS_S3_BUCKET_NAME is not set in configuration")
            
        try:
            response = self.s3_client.generate_presigned_url('put_object',
                                                    Params={'Bucket': self.bucket_name,
                                                            'Key': object_key},
                                                    ExpiresIn=expiration)
        except ClientError as e:
            logger.error(f"Error generating presigned URL for upload: {e}")
            return None
        return response

    def generate_presigned_download_url(self, object_key: str, expiration=3600):
        """Generate a presigned URL for the client to download a file from S3."""
        if not self.bucket_name:
            raise ValueError("AWS_S3_BUCKET_NAME is not set in configuration")
            
        try:
            response = self.s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': self.bucket_name,
                                                            'Key': object_key},
                                                    ExpiresIn=expiration)
        except ClientError as e:
            logger.error(f"Error generating presigned URL for download: {e}")
            return None
        return response

    def delete_object(self, object_key: str):
        """Delete an object from S3."""
        if not self.bucket_name:
            raise ValueError("AWS_S3_BUCKET_NAME is not set in configuration")
            
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=object_key)
        except ClientError as e:
            logger.error(f"Error deleting object from S3: {e}")
            return False
        return True

s3_service = S3Service()
