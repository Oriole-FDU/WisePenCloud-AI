"""Inspect and extract untrusted ZIP, TAR and TAR.GZ files without restoring archive metadata."""

import argparse
from contextlib import ExitStack
import gzip
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
from tempfile import TemporaryFile
import zipfile
import zlib


MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 2_000
BLOCK_SIZE = 64 * 1024


def copy_bounded(source, destination, limit: int) -> int:
    """Limit the actual bytes written, including for compressed TAR data."""
    remaining = limit
    while block := source.read(min(BLOCK_SIZE, remaining + 1)):
        remaining -= len(block)
        if remaining < 0:
            raise ValueError("expanded size exceeds its allowed limit")
        destination.write(block)
    return limit - remaining


def inspect_members(archive, is_zip: bool) -> tuple[dict, int]:
    members = {}
    total = 0
    for index, member in enumerate(archive.infolist() if is_zip else archive, start=1):
        if index > MAX_ENTRIES:
            raise ValueError("archive has too many entries")
        if is_zip:
            name, size, directory = member.orig_filename, member.file_size, member.is_dir()
            mode = stat.S_IFMT(member.external_attr >> 16)
            if (member.flag_bits & 1 or mode not in {0, stat.S_IFDIR if directory else stat.S_IFREG}
                    or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                raise ValueError(f"unsupported ZIP entry: {name!r}")
        else:
            name, size, directory = member.name, member.size, member.isdir()
            if not (directory or member.isfile()) or member.sparse is not None:
                raise ValueError(f"unsupported TAR entry: {name!r}")

        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts or "\\" in name
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or any(":" in part for part in path.parts)
                or len(path.parts) > 32 or len(name.encode("utf-8")) > 1024):
            raise ValueError(f"unsafe archive path: {name!r}")
        if not path.parts and directory and size == 0:
            continue
        if not path.parts or path in members or size < 0 or (directory and size):
            raise ValueError(f"invalid or duplicate archive entry: {name!r}")
        total += size
        if total > MAX_EXPANDED_BYTES:
            raise ValueError("archive exceeds the expanded size limit")
        members[path] = (member, size, directory)

    files = {path for path, (_, _, directory) in members.items() if not directory}
    for path in members:
        if files.intersection(path.parents):
            raise ValueError(f"archive file conflicts with a parent directory: {str(path)!r}")
    return members, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "extract"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--member", action="append", default=[], help="Exact member path; repeat to select files")
    parser.add_argument("--offset", type=int, default=0, help="Inspect listing offset")
    args = parser.parse_args()
    if args.operation == "extract" and args.output is None:
        parser.error("extract requires --output")
    if args.operation == "inspect" and (args.output is not None or args.member or args.offset < 0):
        parser.error("inspect accepts a non-negative --offset and no extraction options")

    try:
        workspace = Path.cwd().resolve()
        source = args.source.resolve()
        source.relative_to(workspace)
        if not source.is_file() or source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("source must be a regular file of at most 100 MiB")
        with ExitStack() as stack:
            stream = stack.enter_context(source.open("rb"))
            is_zip = zipfile.is_zipfile(stream)
            stream.seek(0)
            if is_zip:
                archive = stack.enter_context(zipfile.ZipFile(stream))
            else:
                magic = stream.read(2)
                stream.seek(0)
                if magic == bytes.fromhex("1f8b"):
                    # 先限定压缩 TAR 的完整字节流，包括 TAR 扩展头与填充。
                    expanded = stack.enter_context(TemporaryFile())
                    with gzip.GzipFile(fileobj=stream) as compressed:
                        copy_bounded(compressed, expanded, MAX_EXPANDED_BYTES)
                    expanded.seek(0)
                    stream = expanded
                archive = stack.enter_context(tarfile.open(fileobj=stream, mode="r:"))

            members, total = inspect_members(archive, is_zip)
            if args.operation == "inspect":
                page = list(members.items())[args.offset:args.offset + 12]
                next_offset = args.offset + len(page)
                result = {
                    "count": len(members), "expanded_bytes": total,
                    "entries": [{"path": path.as_posix(), "size": size, "directory": directory}
                                for path, (_, size, directory) in page],
                    "next_offset": next_offset if next_offset < len(members) else None,
                }
            else:
                output = args.output.absolute()
                parent = output.parent.resolve()
                parent.relative_to(workspace)
                output = parent / output.name
                selected = members
                if args.member:
                    # 按检查回执里的完整名称选择普通文件，解压前仍校验整个包。
                    by_name = {path.as_posix(): path for path, (_, _, directory) in members.items() if not directory}
                    if any(name not in by_name for name in args.member):
                        raise ValueError("selected archive member is missing or is not a file")
                    selected = {by_name[name]: members[by_name[name]] for name in args.member}

                relative_output = output.relative_to(workspace)
                output.mkdir(mode=0o700)
                try:
                    for path, (member, size, directory) in selected.items():
                        destination = output.joinpath(*path.parts)
                        if directory:
                            destination.mkdir(parents=True, exist_ok=True)
                        else:
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            payload = archive.open(member) if is_zip else archive.extractfile(member)
                            with payload, destination.open("xb") as target:
                                if copy_bounded(payload, target, size) != size:
                                    raise ValueError(f"archive member size mismatch: {str(path)!r}")
                except (OSError, ValueError, EOFError, zipfile.BadZipFile, tarfile.TarError, zlib.error):
                    shutil.rmtree(output)
                    raise
                result = {"path": relative_output.as_posix(), "entries_extracted": len(selected)}
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, EOFError, zipfile.BadZipFile, tarfile.TarError, zlib.error) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
