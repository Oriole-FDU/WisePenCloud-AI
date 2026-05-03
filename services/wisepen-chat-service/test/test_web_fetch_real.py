#!/usr/bin/env python3
"""
真实测试脚本：覆盖多种内容类型的国内站点降级链路测试

测试维度：
  - PDF 文档直链（巨潮资讯 cninfo）
  - XML 文档（sitemap / RSS）
  - XLSX 文档（统计局数据下载页）
  - PPTX 文档（高校课件下载）
  - DOCX 文档（calibre 示例）
  - 静态 HTML 页面（技术社区 + 开源镜像站）
  - SPA / JS 渲染页面
  - 纯文本 / API 响应（国内公开 API）
  - 国内新闻媒体
  - 强反爬网站
  - 政府网站
  - 高校网站
  - 科技 / 财经媒体
  - 边界场景（404、重定向）

注：所有 URL 均为国内站点（DOCX 示例除外，为国际公开测试资源），排除海外网络影响
"""
import asyncio
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))
sys.path.insert(0, str(SERVICE_ROOT.parent / "wisepen-common" / "src"))

from chat.application.web_fetch.fetch_coordinator import FetchCoordinator

TEST_WEBSITES = [
    # ── PDF 文档直链（巨潮资讯 cninfo） ──────────────────────
    ("劲嘉股份公告 PDF", "http://static.cninfo.com.cn/finalpage/2026-01-23/1224945407.PDF"),
    ("亿纬锂能公告 PDF", "http://static.cninfo.com.cn/finalpage/2025-03-20/1222845668.PDF"),
    ("南京测绘公告 PDF", "http://static.cninfo.com.cn/finalpage/2022-07-06/1213968175.PDF"),
    ("分众传媒公告 PDF", "http://static.cninfo.com.cn/finalpage/2025-04-29/1223365020.PDF"),
    ("万达电影公告 PDF", "http://static.cninfo.com.cn/finalpage/2025-04-29/1223382288.PDF"),

    # ── XML 文档（sitemap / RSS，均已验证） ──────────────────
    ("36氪 sitemap XML", "https://36kr.com/sitemap.xml"),
    ("36氪快讯 sitemap XML", "https://36kr.com/sitemap/newsflashes.xml"),
    ("博客园 RSS XML", "https://www.cnblogs.com/rss"),

    # ── XLSX 文档（统计局数据下载页） ────────────────────────
    ("湖南统计局 Excel 数据", "http://tjj.hunan.gov.cn/hntj/tjfx/hntjnj/201103/t20110315_5295798.html"),

    # ── PPTX 文档（高校课件下载） ────────────────────────────
    ("桂林理工 PPT 课件", "http://jxpt.git.edu.cn/meol/common/script/preview/download_preview.jsp?fileid=42530&resid=20925&lid=12013"),

    # ── DOCX 文档（calibre 示例，已验证） ───────────────────
    ("calibre DOCX 示例", "https://calibre-ebook.com/downloads/demos/demo.docx"),

    # ── 静态 HTML 页面 ──────────────────────────────────────────
    ("开源中国", "https://www.oschina.net/"),
    ("思否 SegmentFault", "https://segmentfault.com/"),
    ("InfoQ 中国", "https://www.infoq.cn/"),
    ("掘金首页", "https://juejin.cn/"),
    ("CSDN 首页", "https://www.csdn.net/"),
    ("博客园", "https://www.cnblogs.com/"),
    ("菜鸟教程", "https://www.runoob.com/"),
    ("清华 TUNA 镜像", "https://mirrors.tuna.tsinghua.edu.cn/"),
    ("中科大镜像", "https://mirrors.ustc.edu.cn/"),

    # ── SPA / JS 渲染页面 ──────────────────────────────────────
    ("B 站", "https://www.bilibili.com/"),
    ("知乎", "https://www.zhihu.com/"),
    ("小红书", "https://www.xiaohongshu.com/"),
    ("豆瓣", "https://www.douban.com/"),
    ("飞书文档", "https://www.feishu.cn/"),

    # ── 纯文本 / API 响应 ──────────────────────────────────────
    ("CNode 主题 API", "https://cnodejs.org/api/v1/topics"),
    ("国家统计局数据", "https://data.stats.gov.cn/"),
    ("国家统计局新闻稿", "https://www.stats.gov.cn/sj/zxfb/"),
    ("证监会信息公开", "http://www.csrc.gov.cn/csrc/c100033/common_list.shtml"),

    # ── 国内新闻媒体 ────────────────────────────────────────────
    ("人民网", "https://www.people.com.cn/"),
    ("新华网", "https://www.xinhuanet.com/"),
    ("央视网", "https://www.cctv.com/"),
    ("澎湃新闻", "https://www.thepaper.cn/"),
    ("网易新闻", "https://news.163.com/"),

    # ── 国内反爬网站 ────────────────────────────────────────────
    ("淘宝", "https://www.taobao.com/"),
    ("京东", "https://www.jd.com/"),
    ("抖音", "https://www.douyin.com/"),
    ("微博", "https://weibo.com/"),
    ("12306", "https://www.12306.cn/"),

    # ── 政府网站（强反爬） ──────────────────────────────────────
    ("中国政府网", "https://www.gov.cn/"),
    ("教育部", "https://www.moe.gov.cn/"),
    ("公安部", "https://www.mps.gov.cn/"),
    ("国家发改委", "https://www.ndrc.gov.cn/"),
    ("财政部", "https://www.mof.gov.cn/"),

    # ── 高校网站 ────────────────────────────────────────────────
    ("清华大学", "https://www.tsinghua.edu.cn/"),
    ("北京大学", "https://www.pku.edu.cn/"),
    ("浙江大学", "https://www.zju.edu.cn/"),
    ("复旦大学", "https://www.fudan.edu.cn/"),

    # ── 科技 / 财经媒体 ────────────────────────────────────────
    ("36氪", "https://36kr.com/"),
    ("虎嗅", "https://www.huxiu.com/"),
    ("东方财富", "https://www.eastmoney.com/"),
    ("第一财经", "https://www.yicai.com/"),
    ("证券时报", "https://www.stcn.com/"),

    # ── 边界场景 ────────────────────────────────────────────────
    ("百度 404", "https://www.baidu.com/this-page-does-not-exist-404"),
    ("知乎重定向", "https://www.zhihu.com/question/999999999999"),
    ("政府网 404", "https://www.gov.cn/404.html"),
]


async def test_website(coordinator: FetchCoordinator, name: str, url: str):
    """测试单个 URL"""
    print(f"\n{'='*80}")
    print(f"测试: {name}")
    print(f"URL:  {url}")
    print(f"{'='*80}")

    try:
        result = await coordinator.fetch(url)

        if result:
            print(f"✅ 成功获取内容")
            print(f"   长度: {len(result)} 字符")
            print(f"   前200字符: {repr(result[:200])}")
            return {
                "name": name,
                "url": url,
                "success": True,
                "length": len(result),
                "preview": result[:200],
            }
        else:
            print(f"❌ 获取失败")
            return {
                "name": name,
                "url": url,
                "success": False,
                "length": 0,
                "preview": "",
            }
    except Exception as e:
        print(f"❌ 异常: {e}")
        return {
            "name": name,
            "url": url,
            "success": False,
            "length": 0,
            "preview": "",
            "error": str(e),
        }


async def main():
    print("\n" + "="*80)
    print("WebFetch 三级降级链路测试（国内站点）")
    print(f"测试用例数: {len(TEST_WEBSITES)}")
    print("="*80)

    coordinator = FetchCoordinator(
        steel_base_url="http://localhost:3000",
        min_content_length=400,
        last_resort_min_length=50,
        static_timeout=15.0,
        browser_timeout=60.0,
    )

    results = []
    for name, url in TEST_WEBSITES:
        result = await test_website(coordinator, name, url)
        results.append(result)
        await asyncio.sleep(1)

    print(f"\n\n{'='*80}")
    print("测试结果统计")
    print(f"{'='*80}")

    success_count = sum(1 for r in results if r["success"])
    total = len(results)
    print(f"总测试数: {total}")
    print(f"成功数:   {success_count}")
    print(f"失败数:   {total - success_count}")
    print(f"成功率:   {success_count/total*100:.1f}%")

    print(f"\n{'='*80}")
    print("分类统计")
    print(f"{'='*80}")

    categories = [
        ("PDF 文档", 0, 5),
        ("XML 文档", 5, 8),
        ("XLSX 文档", 8, 9),
        ("PPTX 文档", 9, 10),
        ("DOCX 文档", 10, 11),
        ("静态 HTML", 11, 20),
        ("SPA/JS 渲染", 20, 25),
        ("纯文本/API", 25, 29),
        ("国内新闻", 29, 34),
        ("国内反爬", 34, 39),
        ("政府网站", 39, 44),
        ("高校网站", 44, 48),
        ("科技/财经", 48, 53),
        ("边界场景", 53, 56),
    ]
    for cat_name, start, end in categories:
        cat_results = results[start:end]
        cat_success = sum(1 for r in cat_results if r["success"])
        print(f"  {cat_name:12s}  {cat_success}/{len(cat_results)} 成功")

    print(f"\n{'='*80}")
    print("详细结果")
    print(f"{'='*80}")
    for r in results:
        status = "✅" if r["success"] else "❌"
        length_info = f" ({r['length']} 字符)" if r["success"] else ""
        print(f"{status} {r['name']:25s}{length_info}")


if __name__ == "__main__":
    asyncio.run(main())
