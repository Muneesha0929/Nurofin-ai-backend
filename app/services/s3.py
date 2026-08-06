import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from app.core.config import settings
import uuid

s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION
)

def upload_file_to_s3(file_obj, filename: str, content_type: str) -> str:
    if not settings.AWS_BUCKET_NAME:
        raise Exception("AWS_BUCKET_NAME is not configured")
    
    unique_filename = f"{uuid.uuid4()}-{filename}"
    
    try:
        s3_client.upload_fileobj(
            file_obj,
            settings.AWS_BUCKET_NAME,
            unique_filename,
            ExtraArgs={
                "ContentType": content_type
            }
        )
        return unique_filename
    except NoCredentialsError:
        raise Exception("AWS credentials not available")
    except ClientError as e:
        raise Exception(f"AWS S3 error: {str(e)}")

def get_presigned_url(s3_key: str, expiration=3600) -> str:
    if not settings.AWS_BUCKET_NAME:
        raise Exception("AWS_BUCKET_NAME is not configured")
        
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': settings.AWS_BUCKET_NAME,
                                                            'Key': s3_key},
                                                    ExpiresIn=expiration)
    except ClientError as e:
        raise Exception(f"AWS S3 error: {str(e)}")
    return response

def delete_file_from_s3(s3_key: str):
    if not settings.AWS_BUCKET_NAME:
        return
        
    try:
        s3_client.delete_object(Bucket=settings.AWS_BUCKET_NAME, Key=s3_key)
    except ClientError:
        pass
