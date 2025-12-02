from google.oauth2 import service_account
import google.auth
import google.auth.transport.requests

# Path to your service account key file
SERVICE_ACCOUNT_KEY_FILE = '/Users/shaharrozolio/Downloads/broker-460917-aec0ef945ec7.json'

# Define the scope(s) needed for Gemini Enterprise
# Common scopes include 'https://www.googleapis.com/auth/cloud-platform'
# or more specific scopes if available for Gemini Enterprise.
SCOPES = ['https://www.googleapis.com/auth/cloud-platform']

try:
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_KEY_FILE, scopes=SCOPES
    )
    # Refresh the credentials to get an access token
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    access_token = credentials.token
    print(f"Access Token: {access_token}")
except Exception as e:
    print(f"Error obtaining token: {e}")
