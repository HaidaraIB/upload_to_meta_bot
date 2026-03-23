import asyncio
import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from google_drive.config import GoogleDriveConfig
from google_drive.errors import GoogleDriveConfigError, GoogleDriveUploadError

logger = logging.getLogger(__name__)


class GoogleDriveService:
    """
    Thread-safe singleton wrapper around Google Drive API client.

    The Google API client is synchronous, so public async methods delegate
    blocking operations to a worker thread.
    """

    _instance: "GoogleDriveService | None" = None
    _instance_lock = Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._service = None
                    cls._instance._service_lock = Lock()
        return cls._instance

    def _get_drive_service(self):
        if self._service is not None:
            return self._service

        with self._service_lock:
            if self._service is None:
                self._service = self._build_drive_service()
        return self._service

    def _build_drive_service(self):
        credentials_path = GoogleDriveConfig.CREDENTIALS_FILE
        refresh_token_path = GoogleDriveConfig.REFRESH_TOKEN_FILE

        if not credentials_path.exists():
            raise GoogleDriveConfigError(
                f"Google Drive credentials file not found: {credentials_path}"
            )
        if not refresh_token_path.exists():
            raise GoogleDriveConfigError(
                f"Google Drive refresh token file not found: {refresh_token_path}"
            )

        try:
            with credentials_path.open("r", encoding="utf-8") as f:
                client_config = json.load(f)
            with refresh_token_path.open("r", encoding="utf-8") as f:
                refresh_token = f.read().strip()
        except OSError as exc:
            raise GoogleDriveConfigError(
                "Unable to read Google Drive credentials files."
            ) from exc

        installed_config = client_config.get("installed", {})
        client_id = installed_config.get("client_id")
        client_secret = installed_config.get("client_secret")
        if not client_id or not client_secret or not refresh_token:
            raise GoogleDriveConfigError(
                "Google Drive credentials or refresh token content is invalid."
            )

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=GoogleDriveConfig.SCOPES,
        )
        creds.refresh(Request())

        return build(
            "drive",
            "v3",
            credentials=creds,
            static_discovery=False,
            cache_discovery=False,
            always_use_jwt_access=True,
        )

    def _upload_file_sync(
        self, file_path: str | Path, folder_id: str
    ) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        file_metadata = {"name": path.name, "parents": [folder_id]}
        media = MediaFileUpload(str(path))

        try:
            uploaded_file = (
                self._get_drive_service()
                .files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id,name,webViewLink,mimeType",
                )
                .execute()
            )
        except Exception as exc:
            logger.exception("Failed uploading file to Google Drive: %s", path.name)
            raise GoogleDriveUploadError(
                f"Failed uploading file to Google Drive: {path.name}"
            ) from exc

        return {
            "id": uploaded_file.get("id"),
            "name": uploaded_file.get("name"),
            "mime_type": uploaded_file.get("mimeType"),
            "web_view_link": uploaded_file.get("webViewLink"),
        }

    async def upload_file_async(
        self, file_path: str | Path, folder_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._upload_file_sync, file_path, folder_id)


google_drive_service = GoogleDriveService()
