#!/usr/bin/env python3
# read_first_json.py
import gzip, io, json, sys, argparse

def read_first_json_object(path, chunk_size=64 * 1024):
    """流式读取 gzip 文件中第一个完整 JSON 对象。"""
    with gzip.open(path, 'rb') as gz:
        text = io.TextIOWrapper(gz, encoding='utf-8-sig', newline='')
        parts, depth, in_str, escape, started = [], 0, False, False, False

        while True:
            chunk = text.read(chunk_size)      # 每次解压一小块
            if not chunk:
                break
            for ch in chunk:
                if not started:
                    if ch.isspace() or ch in ',[':   # 允许 JSONL 或数组开头
                        continue
                    if ch == '{':
                        started, depth = True, 1
                        parts.append(ch)
                        continue
                    raise ValueError(f"对象开始前的非法字符: {ch!r}")
                parts.append(ch)
                if in_str:
                    if escape:            escape = False
                    elif ch == '\\':      escape = True
                    elif ch == '"':       in_str = False
                else:
                    if ch == '"':         in_str = True
                    elif ch == '{':       depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            return json.loads(''.join(parts))
        raise ValueError("文件结束但未读到完整 JSON 对象")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--pretty', action='store_true')
    ap.add_argument('--out', help='输出文件，默认 stdout')
    a = ap.parse_args()

    obj = read_first_json_object(a.path)
    s = json.dumps(obj, ensure_ascii=False, indent=2 if a.pretty else None)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(s)
    else:
        sys.stdout.write(s + '\n')

if __name__ == '__main__':
    main()