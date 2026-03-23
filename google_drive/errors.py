class GoogleDriveError(Exception):
    """Base exception for Google Drive service errors."""


class GoogleDriveConfigError(GoogleDriveError):
    """Raised when Google Drive configuration files are missing or invalid."""


class GoogleDriveUploadError(GoogleDriveError):
    """Raised when file upload to Google Drive fails."""
