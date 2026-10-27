"""离线 S2 release 与差分清单读取；签名对象文件由离线下载步骤处理。"""

from scholar.core.s2_transport import S2Transport


class S2DatasetsClient:
    def __init__(self, transport: S2Transport):
        self.transport = transport

    async def release(self, release_id: str) -> dict:
        return await self.transport.request("GET", f"datasets/v1/release/{release_id}")

    async def dataset(self, release_id: str, dataset: str) -> dict:
        return await self.transport.request(
            "GET", f"datasets/v1/release/{release_id}/dataset/{dataset}"
        )

    async def diffs(self, start_release: str, end_release: str, dataset: str) -> dict:
        return await self.transport.request(
            "GET", f"datasets/v1/diffs/{start_release}/to/{end_release}/{dataset}"
        )
