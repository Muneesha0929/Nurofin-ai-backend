import os
import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION")
AWS_S3_BUCKET_NAME = os.environ.get("AWS_S3_BUCKET_NAME")

print("Checking AWS Configuration...")
print(f"Bucket: {AWS_S3_BUCKET_NAME}")
print(f"Region: {AWS_REGION}")
print(f"Access Key ID length: {len(AWS_ACCESS_KEY_ID) if AWS_ACCESS_KEY_ID else 0}")
print(f"Secret Access Key length: {len(AWS_SECRET_ACCESS_KEY) if AWS_SECRET_ACCESS_KEY else 0}")

if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    print("Error: AWS keys are missing in the environment.")
    exit(1)

try:
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION
    )
    
    # Test uploading a small text string
    print("Attempting to upload a test file to S3...")
    test_key = "test_connection.txt"
    s3_client.put_object(
        Bucket=AWS_S3_BUCKET_NAME,
        Key=test_key,
        Body=b"This is a test file to verify S3 permissions."
    )
    print("Upload successful!")
    
    # Clean up the test file
    print("Attempting to delete the test file...")
    s3_client.delete_object(
        Bucket=AWS_S3_BUCKET_NAME,
        Key=test_key
    )
    print("Delete successful! AWS S3 is fully functional.")
    
except NoCredentialsError:
    print("Error: AWS credentials not found or invalid format.")
except ClientError as e:
    print(f"AWS S3 Error: {e.response['Error']['Message']}")
    print(f"Error Code: {e.response['Error']['Code']}")
except Exception as e:
    print(f"Unexpected Error: {str(e)}")
