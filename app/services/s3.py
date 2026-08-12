import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from app.core.config import settings
import uuid
import os
import shutil

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")

def get_s3_client():
    if not settings.AWS_S3_BUCKET_NAME:
        return None
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION
    )

def upload_file_to_s3(file_obj, filename: str, content_type: str) -> str:
    unique_filename = f"{uuid.uuid4()}-{filename}"
    s3_client = get_s3_client()
    
    if not s3_client:
        # Fallback to local storage
        if not os.path.exists(UPLOAD_DIR):
            os.makedirs(UPLOAD_DIR)
        
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file_obj, buffer)
        return f"local/{unique_filename}"
    
    try:
        s3_client.upload_fileobj(
            file_obj,
            settings.AWS_S3_BUCKET_NAME,
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
    s3_client = get_s3_client()
    
    if not s3_client or s3_key.startswith("local/"):
        # Fallback to local URL
        filename = s3_key.replace("local/", "")
        return f"/api/v1/documents/download/{filename}"
        
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': settings.AWS_S3_BUCKET_NAME,
                                                            'Key': s3_key},
                                                    ExpiresIn=expiration)
    except ClientError as e:
        raise Exception(f"AWS S3 error: {str(e)}")
    return response

def delete_file_from_s3(s3_key: str):
    s3_client = get_s3_client()
    
    if not s3_client or s3_key.startswith("local/"):
        filename = s3_key.replace("local/", "")
        file_path = os.path.join(UPLOAD_DIR, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        return
        
    try:
        s3_client.delete_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key=s3_key)
    except ClientError:
        pass

