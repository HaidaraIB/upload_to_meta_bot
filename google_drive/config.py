from pathlib import Path


class GoogleDriveConfig:
    SCOPES = ["https://www.googleapis.com/auth/drive"]

    _base_dir = Path(__file__).resolve().parent
    _credentials_dir = _base_dir / "credentials"
    
    CREDENTIALS_FILE = Path(str(_credentials_dir / "credentials.json"))
    REFRESH_TOKEN_FILE = Path(str(_credentials_dir / "refresh_token.txt"))
