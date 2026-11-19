import asyncio
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.exceptions import ValidationException
from app.modules.common.models import generate_uuid7

ALLOWED_BRANDING_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class StorageService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def save_school_branding_asset(
        self,
        *,
        school_slug: str,
        asset_type: str,
        content_type: str,
        content: bytes,
    ) -> str:
        extension = ALLOWED_BRANDING_CONTENT_TYPES.get(content_type)
        if extension is None:
            raise ValidationException(
                "Please upload a PNG, JPEG, or WebP image. Other image formats are not supported yet."
            )
        if not content:
            raise ValidationException(
                "The uploaded image is empty. Please choose a different file."
            )
        if len(content) > self.settings.BRANDING_UPLOAD_MAX_BYTES:
            raise ValidationException(
                "This image is too large. Please upload an image smaller than 2 MB."
            )

        safe_slug = "".join(char for char in school_slug.lower() if char.isalnum() or char in "-_")
        if not safe_slug:
            safe_slug = "school"

        filename = f"{generate_uuid7()}{extension}"
        relative_path = Path("schools") / safe_slug / "branding" / asset_type / filename
        if self.settings.STORAGE_PROVIDER == "local" and self.settings.ENVIRONMENT.lower() not in {
            "development",
            "test",
        }:
            # Staging/production without cloud storage: validate and return a stub path
            # without writing files to the ephemeral server filesystem.
            return relative_path.as_posix()

        destination = Path(self.settings.STORAGE_LOCAL_DIR) / relative_path
        await self._write_bytes(destination, content)
        return relative_path.as_posix()

    def public_url(self, storage_path: str | None) -> str | None:
        if storage_path is None:
            return None
        base_url = self.settings.STORAGE_PUBLIC_BASE_URL
        if not base_url:
            return storage_path
        return f"{base_url.rstrip('/')}/{storage_path.lstrip('/')}"

    async def _write_bytes(self, destination: Path, content: bytes) -> None:
        await asyncio.to_thread(self._write_bytes_sync, destination, content)

    def _write_bytes_sync(self, destination: Path, content: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


storage_service = StorageService(get_settings())
