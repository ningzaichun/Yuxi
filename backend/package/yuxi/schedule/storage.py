"""Private object-storage adapter for canonical Schedule snapshots."""

from yuxi.storage.minio.client import MinIOClient, get_minio_client

SCHEDULE_BUCKET = "schedule-snapshots"


class ScheduleSnapshotStore:
    def __init__(self, client: MinIOClient | None = None) -> None:
        self._client = client or get_minio_client()

    async def upload(self, object_name: str, data: bytes) -> None:
        await self._client.aupload_file(
            SCHEDULE_BUCKET,
            object_name,
            data,
            content_type="application/json",
        )

    async def download(self, object_name: str) -> bytes:
        return await self._client.adownload_file(SCHEDULE_BUCKET, object_name)
