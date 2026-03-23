from google_auth_oauthlib.flow import InstalledAppFlow
from config import GoogleDriveConfig

flow = InstalledAppFlow.from_client_secrets_file(
    GoogleDriveConfig.CREDENTIALS_FILE, scopes=GoogleDriveConfig.SCOPES
)

credentials = flow.run_local_server(port=0)

with open(GoogleDriveConfig.REFRESH_TOKEN_FILE, "w") as f:
    f.write(credentials.refresh_token)

print(f"Refresh token saved to {GoogleDriveConfig.REFRESH_TOKEN_FILE}")
