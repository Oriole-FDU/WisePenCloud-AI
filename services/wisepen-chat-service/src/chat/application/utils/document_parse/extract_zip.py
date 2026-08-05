from __future__ import annotations

import argparse
import multiprocessing
import ntpath
import os
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from multiprocessing.connection import Connection
from pathlib import Path, PurePosixPath

from .errors import ArchiveExtractionError

try:
    import resource  # POSIX-only；Windows 无此模块，回退为仅超时防护
except ImportError:
    resource = None

# ---------------------------------------------------------------------------
# 安全资源上限常量
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 1024 * 1024
# 输入压缩包自身大小上限：不限制原始体量的话，无法阻止"极小声明信息 + 巨量
# central directory 记录"式的构造——该攻击在 zipfile 解析阶段即可耗尽内存，
# 早于下面任何基于 entry 内容的检查生效
_MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
# 子进程虚拟地址空间硬上限：防御"中央目录炸弹"——zipfile 在打开文件时会
# 一次性解析全部 central directory 并实例化为 ZipInfo 列表，此过程发生在
# _MAX_ENTRIES 检查之前，仅靠该检查无法防止解析阶段本身的内存暴涨
_MAX_WORKER_VIRTUAL_MEMORY_BYTES = 512 * 1024 * 1024
# 条目总数上限：阻止"百万小文件"式的 inode / 文件系统耗尽攻击
_MAX_ENTRIES = 10_000
# 单文件解压后大小上限
_MAX_SINGLE_FILE_BYTES = 256 * 1024 * 1024
# 全部条目解压总大小上限
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
# 压缩比上限：识别"极小压缩体解压出超大内容"的典型 Zip Bomb 特征
_MAX_COMPRESSION_RATIO = 200
# 路径 / 单段长度上限：避免触发文件系统或工具链的未定义行为
_MAX_PATH_BYTES = 1024
_MAX_COMPONENT_BYTES = 255
# 解压整体超时：防止解压算法级拒绝服务（死循环 / 极慢流）
_EXTRACTION_TIMEOUT_SECONDS = 120
_PROCESS_STOP_TIMEOUT_SECONDS = 5

# 压缩方法白名单：未知方法可能触发解释器 / 第三方库中的未定义行为
_ALLOWED_COMPRESSION_METHODS = {
    zipfile.ZIP_STORED,
    zipfile.ZIP_DEFLATED,
    zipfile.ZIP_BZIP2,
    zipfile.ZIP_LZMA,
}

# Windows 保留设备名 / 非法字符：写入会被 NT 内核劫持或直接拒绝。
# 即使部署在 Linux 也提前过滤，避免跨平台同步时的二次风险
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_ILLEGAL_CHARS = set('<>:"|?*')


def extract_zip(
    file_path: str | Path,
    *,
    output_dir: str | Path,
) -> Path:
    """在隔离子进程中解压 ZIP 到新建的独立目录。"""
    file_path = Path(file_path).resolve()
    if not file_path.is_file():
        raise FileNotFoundError(file_path)
    # 在创建任何目录 / 启动子进程之前先拒绝超大输入，避免中央目录炸弹
    # 有机会消耗资源
    if file_path.stat().st_size > _MAX_ARCHIVE_FILE_BYTES:
        raise ArchiveExtractionError(
            f"ZIP file exceeds the maximum allowed size of "
            f"{_MAX_ARCHIVE_FILE_BYTES} bytes."
        )

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    # 随机命名的全新临时目录：避免路径可预测 + 符号链接竞速
    extraction_dir = Path(
        tempfile.mkdtemp(prefix="zip_extract_", dir=output_dir)
    ).resolve()
    extraction_dir.chmod(0o700)  # 仅属主可读写执行

    # spawn（而非 fork）：子进程不继承父进程内存布局，zipfile 解析阶段的
    # 潜在漏洞或资源暴涨不会直接污染调用者进程
    context = multiprocessing.get_context("spawn")
    # 单向 Pipe：仅 worker → 主进程回传错误信息，防止反向注入
    error_reader, error_writer = context.Pipe(duplex=False)
    process = context.Process(
        target=_extract_worker,
        args=(file_path, extraction_dir, error_writer),
        daemon=True,
    )
    try:
        process.start()
        error_writer.close()
        process.join(_EXTRACTION_TIMEOUT_SECONDS)
        if process.is_alive():
            # 超时两阶段终止：先 SIGTERM 允许清理，再 SIGKILL 兜底
            process.terminate()
            process.join(_PROCESS_STOP_TIMEOUT_SECONDS)
            if process.is_alive():
                process.kill()
                process.join()
            raise ArchiveExtractionError(
                f"ZIP extraction exceeded {_EXTRACTION_TIMEOUT_SECONDS} seconds."
            )

        if process.exitcode != 0:
            message = (
                error_reader.recv()
                if error_reader.poll(_PROCESS_STOP_TIMEOUT_SECONDS)
                else "ZIP extraction worker failed."
            )
            raise ArchiveExtractionError(message)
    except BaseException:
        # 任何失败路径（超时 / 崩溃 / 中断）都清理隔离目录，不留残留
        shutil.rmtree(extraction_dir, ignore_errors=True)
        raise
    finally:
        error_reader.close()
        error_writer.close()
        process.close()

    return extraction_dir


def _extract_worker(
    file_path: Path,
    extraction_dir: Path,
    error_writer: Connection,
) -> None:
    """子进程入口。异常以字符串（而非异常对象）回传，避免恶意构造的异常
    对象在主进程反序列化时触发 RCE。"""
    if resource is not None:
        try:
            # 硬性限制虚拟地址空间：即使 central directory 被构造成海量
            # 条目，zipfile 在解析阶段命中此上限即抛 MemoryError 退出，
            # 不会拖垮宿主机整体内存；早于 _MAX_ENTRIES 检查生效
            resource.setrlimit(
                resource.RLIMIT_AS,
                (_MAX_WORKER_VIRTUAL_MEMORY_BYTES, _MAX_WORKER_VIRTUAL_MEMORY_BYTES),
            )
        except (ValueError, OSError):
            pass  # 部分平台（如 macOS）不支持 RLIMIT_AS，退化为仅依赖超时防护

    try:
        _extract_contents(file_path, extraction_dir)
    except BaseException as exc:
        try:
            error_writer.send(f"{type(exc).__name__}: {exc}")
        finally:
            error_writer.close()
        raise SystemExit(1) from exc
    error_writer.close()


def _extract_contents(file_path: Path, extraction_dir: Path) -> None:
    """流式写盘。运行时校验与声明值校验双重生效：即使 ZIP 元数据被篡改，
    实际写入字节仍会被实时拦截。"""
    try:
        with zipfile.ZipFile(file_path, metadata_encoding="cp437") as archive:
            planned_entries = _validate_entries(archive.infolist(), extraction_dir)
            written_total = 0
            for info, target, is_directory in planned_entries:
                if is_directory:
                    target.mkdir(parents=True, exist_ok=False)
                    target.chmod(0o700)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                written_file = 0
                # "xb"：O_CREAT | O_EXCL，目标必须全新不存在，禁止覆盖任何
                # 既有路径（防止 TOCTOU 覆盖攻击）
                with archive.open(info, "r") as source, target.open("xb") as output:
                    while chunk := source.read(_CHUNK_SIZE):
                        written_file += len(chunk)
                        written_total += len(chunk)
                        # 与 _validate_entries 中基于声明值的预检形成
                        # "声明 + 实际"双重防线，防止元数据撒谎
                        if written_file > _MAX_SINGLE_FILE_BYTES:
                            raise ArchiveExtractionError(
                                f"ZIP entry exceeds the per-file limit: {info.filename!r}."
                            )
                        if written_total > _MAX_TOTAL_BYTES:
                            raise ArchiveExtractionError(
                                "ZIP exceeds the total extraction size limit."
                            )
                        output.write(chunk)

                if written_file != info.file_size:
                    raise ArchiveExtractionError(
                        f"ZIP entry size differs from its metadata: {info.filename!r}."
                    )
                # 忽略 ZIP 内记录的 external_attr 权限位，统一强制收紧，
                # 避免写入 setuid / 全局可写等高风险权限
                target.chmod(0o600)
    except ArchiveExtractionError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ArchiveExtractionError(f"Failed to extract ZIP: {exc}.") from exc


def _validate_entries(
    entries: list[zipfile.ZipInfo],
    extraction_dir: Path,
) -> list[tuple[zipfile.ZipInfo, Path, bool]]:
    """解压前的全量条目静态预检，在任何磁盘写入前筛出恶意条目。"""
    if len(entries) > _MAX_ENTRIES:
        raise ArchiveExtractionError(f"ZIP contains more than {_MAX_ENTRIES} entries.")

    root = extraction_dir.resolve()
    planned_entries: list[tuple[zipfile.ZipInfo, Path, bool]] = []
    targets: dict[str, bool] = {}  # key: 大小写折叠后的规范路径；value: 是否为目录
    declared_total = 0

    for info in entries:
        raw_name = getattr(info, "orig_filename", info.filename)
        target = _resolve_entry_path(root, raw_name)
        mode = info.external_attr >> 16  # 高 16 位承载 unix mode_t（不可信，仅用于检测）
        file_type = stat.S_IFMT(mode)
        is_directory = info.is_dir() or stat.S_ISDIR(mode)

        # 加密条目：密文无法被本脚本校验实际内容/大小，直接拒绝
        if info.flag_bits & 0x1:
            raise ArchiveExtractionError(f"Encrypted ZIP entry is not allowed: {raw_name!r}.")
        # 符号链接：指向目录外的链接是 Zip Slip 的典型变种，禁止保留链接语义
        if stat.S_ISLNK(mode):
            raise ArchiveExtractionError(f"Symbolic link ZIP entry is not allowed: {raw_name!r}.")
        # 设备/管道/socket 等特殊文件：防止创建可被后续逻辑滥用的节点
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ArchiveExtractionError(f"Special-file ZIP entry is not allowed: {raw_name!r}.")
        if info.compress_type not in _ALLOWED_COMPRESSION_METHODS:
            raise ArchiveExtractionError(f"Unsupported ZIP compression method for {raw_name!r}.")
        if is_directory and info.file_size:
            raise ArchiveExtractionError(f"Directory ZIP entry contains data: {raw_name!r}.")

        if not is_directory:
            if info.file_size > _MAX_SINGLE_FILE_BYTES:
                raise ArchiveExtractionError(f"ZIP entry exceeds the per-file limit: {raw_name!r}.")
            declared_total += info.file_size
            if declared_total > _MAX_TOTAL_BYTES:
                raise ArchiveExtractionError("ZIP declares more than the total extraction size limit.")
            if info.file_size and not info.compress_size:
                raise ArchiveExtractionError(f"ZIP entry has an invalid compressed size: {raw_name!r}.")
            # 高压缩比是典型 Zip Bomb 特征（如极小 DEFLATE 流展开为巨量数据）
            if info.compress_size and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO:
                raise ArchiveExtractionError(f"ZIP entry exceeds the compression-ratio limit: {raw_name!r}.")

        # 路径冲突：不同 entry 规范化后落到同一路径，可能被用于制造
        # "不同解压工具看到不同内容"的差异化攻击
        target_key = os.path.normcase(str(target)).casefold()
        if target_key in targets:
            raise ArchiveExtractionError(f"ZIP entries resolve to the same path: {raw_name!r}.")
        targets[target_key] = is_directory
        planned_entries.append((info, target, is_directory))

    # 父子路径类型冲突：例如同时存在 "a"（文件）与 "a/b"（文件），后者无法落盘
    for _, target, _ in planned_entries:
        parent = target.parent
        while parent != root:
            parent_type = targets.get(os.path.normcase(str(parent)).casefold())
            if parent_type is False:
                raise ArchiveExtractionError(f"ZIP file conflicts with a parent directory: {target.name!r}.")
            parent = parent.parent

    # 目录优先、路径由浅到深排序，确保写盘时父目录先就绪
    return sorted(planned_entries, key=lambda item: (not item[2], len(item[1].parts)))


def _resolve_entry_path(root: Path, raw_name: str) -> Path:
    """单条目路径安全解析——核心 Zip Slip 防线，返回路径必须严格位于 root 内。"""
    if not raw_name or "\x00" in raw_name:
        raise ArchiveExtractionError("ZIP entry has an empty or null filename.")
    # 拒绝控制/格式类字符（含 RTL/LTR 嵌入符），防止文件名视觉欺骗
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in raw_name):
        raise ArchiveExtractionError(f"ZIP entry contains control characters: {raw_name!r}.")

    normalized_name = unicodedata.normalize("NFC", raw_name).replace("\\", "/")
    normalized_name = normalized_name[:-1] if normalized_name.endswith("/") else normalized_name
    if not normalized_name or normalized_name.startswith("/") or ntpath.splitdrive(normalized_name)[0]:
        raise ArchiveExtractionError(f"ZIP entry uses an absolute path: {raw_name!r}.")
    if len(normalized_name.encode("utf-8")) > _MAX_PATH_BYTES:
        raise ArchiveExtractionError(f"ZIP entry path is too long: {raw_name!r}.")

    parts = normalized_name.split("/")
    # 空段 / "." / ".."：封杀所有形式的路径穿越
    if any(part in {"", ".", ".."} for part in parts):
        raise ArchiveExtractionError(f"ZIP entry contains an unsafe path component: {raw_name!r}.")
    for part in parts:
        if len(part.encode("utf-8")) > _MAX_COMPONENT_BYTES:
            raise ArchiveExtractionError(f"ZIP entry filename is too long: {raw_name!r}.")
        # 末尾空格/点号会被 Windows 静默裁剪，可能造成规范化前后路径不一致
        if part.endswith((" ", ".")) or any(char in _WINDOWS_ILLEGAL_CHARS for char in part):
            raise ArchiveExtractionError(f"ZIP entry contains an illegal filename: {raw_name!r}.")
        # "CON"/"CON.txt" 等在 Windows 会被内核识别为设备
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            raise ArchiveExtractionError(f"ZIP entry uses a reserved filename: {raw_name!r}.")

    target = (root / Path(*PurePosixPath(normalized_name).parts)).resolve()
    try:
        common_path = Path(os.path.commonpath((root, target)))
    except ValueError as exc:
        # Windows 跨盘符会在此抛 ValueError，同样视为逃逸
        raise ArchiveExtractionError(f"ZIP entry escapes the extraction directory: {raw_name!r}.") from exc
    if common_path != root:
        # 兜底防线：无论前面如何规范化，最终必须以 root 为公共前缀
        raise ArchiveExtractionError(f"ZIP entry escapes the extraction directory: {raw_name!r}.")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely extract a ZIP into a new isolated directory.")
    parser.add_argument("file_path", type=Path)
    args = parser.parse_args()
    output_dir = args.file_path.with_suffix("")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        extraction_dir = extract_zip(args.file_path, output_dir=output_dir)
    except (ArchiveExtractionError, FileNotFoundError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(f"Archive extracted to {extraction_dir}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
