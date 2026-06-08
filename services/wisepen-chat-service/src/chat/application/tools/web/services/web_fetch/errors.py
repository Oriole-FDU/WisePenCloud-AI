class WebFetchError(Exception):
    """Web 抓取模块的基础异常类。"""
    pass


class UnsupportedMediaError(WebFetchError):
    """当目标 URL 返回媒体/文档资源而非可解析网页文本时抛出的异常。"""

    def __init__(self, url: str, media_type: str):
        """记录触发异常的 URL 和检测到的媒体类型。"""
        self.url = url
        self.media_type = media_type
        super().__init__(
            f"该 URL 返回的是 {media_type} 等媒体资源，不适合作为网页正文抓取。"
        )