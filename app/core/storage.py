import asyncio
import hashlib
import time
from pathlib import Path

import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import DomainException, ValidationException
from app.modules.common.models import generate_uuid7

ALLOWED_BRANDING_CONTENT_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def detect_image_format(content: bytes) -> tuple[str, str] | None:
    """Detect PNG, JPEG, or WebP from binary magic bytes (never trust filename/MIME alone)."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


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
        normalized_mime = "image/jpeg" if content_type == "image/jpg" else content_type.lower()
        if normalized_mime not in ALLOWED_BRANDING_CONTENT_TYPES:
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
        detected = detect_image_format(content)
        if detected is None:
            raise ValidationException(
                "The uploaded file does not appear to be a valid PNG, JPEG, or WebP image."
            )
        detected_mime, extension = detected
        if detected_mime != normalized_mime:
            raise ValidationException(
                "The file's content does not match its declared image type. Please upload a valid PNG, JPEG, or WebP image."
            )

        safe_slug = "".join(char for char in school_slug.lower() if char.isalnum() or char in "-_")
        if not safe_slug:
            safe_slug = "school"

        filename = f"{generate_uuid7()}{extension}"
        relative_path = Path("schools") / safe_slug / "branding" / asset_type / filename
        provider = self.settings.STORAGE_PROVIDER.lower()

        if provider == "local":
            if self.settings.ENVIRONMENT.lower() in {"staging", "production", "prod"}:
                return relative_path.as_posix()
            destination = Path(self.settings.STORAGE_LOCAL_DIR) / relative_path
            await self._write_bytes(destination, content)
            return relative_path.as_posix()

        if provider == "cloudinary":
            return await self._upload_cloudinary(
                public_id=relative_path.with_suffix("").as_posix(),
                content=content,
                content_type=content_type,
                filename=filename,
            )

        if provider == "s3":
            return await asyncio.to_thread(
                self._upload_s3_sync,
                key=relative_path.as_posix(),
                content=content,
                content_type=content_type,
            )

        raise DomainException(
            code="UNSUPPORTED_STORAGE_PROVIDER",
            message="The configured file storage provider is not supported.",
            status_code=500,
        )

    def public_url(self, storage_path: str | None) -> str | None:
        if storage_path is None:
            return None
        if storage_path.startswith(("http://", "https://")):
            return storage_path
        base_url = self.settings.STORAGE_PUBLIC_BASE_URL
        if not base_url:
            return storage_path
        return f"{base_url.rstrip('/')}/{storage_path.lstrip('/')}"

    async def _upload_cloudinary(
        self,
        *,
        public_id: str,
        content: bytes,
        content_type: str,
        filename: str,
    ) -> str:
        cloud_name = self.settings.CLOUDINARY_CLOUD_NAME
        api_key = self.settings.CLOUDINARY_API_KEY
        api_secret = self.settings.CLOUDINARY_API_SECRET
        if not cloud_name or not api_key or not api_secret:
            raise DomainException(
                code="STORAGE_NOT_CONFIGURED",
                message="Cloud image storage credentials are not configured.",
                status_code=503,
            )
        timestamp = str(int(time.time()))
        to_sign = f"public_id={public_id}&timestamp={timestamp}{api_secret}"
        signature = hashlib.sha1(to_sign.encode("utf-8")).hexdigest()
        url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
        try:
            async with httpx.AsyncClient(timeout=20) as http:
                resp = await http.post(
                    url,
                    data={
                        "public_id": public_id,
                        "timestamp": timestamp,
                        "api_key": api_key,
                        "signature": signature,
                    },
                    files={"file": (filename, content, content_type)},
                )
        except httpx.HTTPError as exc:
            raise DomainException(
                code="STORAGE_UPLOAD_FAILED",
                message="We could not upload this image right now. Please try again.",
                status_code=502,
            ) from exc
        if resp.status_code >= 400:
            raise DomainException(
                code="STORAGE_UPLOAD_FAILED",
                message="We could not upload this image right now. Please try again.",
                status_code=502,
            )
        return str(resp.json().get("secure_url") or public_id)

    def _upload_s3_sync(self, *, key: str, content: bytes, content_type: str) -> str:
        if not self.settings.S3_BUCKET:
            raise DomainException(
                code="STORAGE_NOT_CONFIGURED",
                message="S3 storage bucket is not configured.",
                status_code=503,
            )
        try:
            import boto3  # type: ignore[import-untyped]
            from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
        except ImportError as exc:
            raise DomainException(
                code="STORAGE_NOT_CONFIGURED",
                message="S3 storage requires boto3 to be installed.",
                status_code=503,
            ) from exc

        client = boto3.client(
            "s3",
            region_name=self.settings.S3_REGION,
            endpoint_url=self.settings.S3_ENDPOINT_URL,
            aws_access_key_id=self.settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=self.settings.AWS_SECRET_ACCESS_KEY,
        )
        try:
            client.put_object(
                Bucket=self.settings.S3_BUCKET,
                Key=key,
                Body=content,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise DomainException(
                code="STORAGE_UPLOAD_FAILED",
                message="We could not upload this image right now. Please try again.",
                status_code=502,
            ) from exc
        return key

    async def _write_bytes(self, destination: Path, content: bytes) -> None:
        await asyncio.to_thread(self._write_bytes_sync, destination, content)

    def _write_bytes_sync(self, destination: Path, content: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


storage_service = StorageService(get_settings())
