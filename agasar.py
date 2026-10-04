# -*- coding: utf-8 -*-
"""asar (Electron 归档) 解析器 — 纯标准库。

负责三件事：
  1. 解析 asar 头，列出归档内文件
  2. 按名字读取单个文件内容
  3. 整包解包到目录（带条目路径遏制校验，防 asar-slip）
"""
import json
import struct
from pathlib import Path, PurePosixPath


class AsarError(Exception):
    pass


def _safe_parts(name):
    """拆分校验 asar 条目路径，拒绝盘符/绝对路径/上跳目录。"""
    parts = PurePosixPath(name).parts
    if not parts:
        raise AsarError("asar 条目路径为空")
    for part in parts:
        if part in (".", ".."):
            raise AsarError(f"asar 条目含非法路径段: {name}")
        if ":" in part or part.startswith("/") or part.startswith(chr(92)):
            raise AsarError(f"asar 条目含非法路径段: {name}")
    return parts


class Asar:
    def __init__(self, path):
        self.path = Path(path)
        # 单句柄：先读 header 再保留同一个文件对象，省一次 open，
        # 也消除两次 open 之间文件被替换的理论窗口
        self._f = self.path.open("rb")
        head = self._f.read(16)
        if len(head) != 16:
            self._f.close()
            raise AsarError(f"文件太小，不是 asar: {path}")
        _magic, header_pickle_size, _u1, json_size = struct.unpack("<4I", head)
        self.header = json.loads(self._f.read(json_size).decode("utf-8"))
        # 内容区起始 = 前 8 字节 + 头部 pickle 总长（与 MIMICTE 同款算法，已实测验证）
        self.base_offset = 8 + header_pickle_size

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _walk(self):
        def rec(node, prefix):
            for name, info in node.items():
                child = prefix + "/" + name if prefix else name
                if "files" in info:
                    yield from rec(info["files"], child)
                elif "offset" in info:
                    yield child, info
        yield from rec(self.header.get("files", {}), "")

    def read(self, name):
        """按相对路径读取归档内文件内容。"""
        node = self.header.get("files", {})
        parts = name.split("/")
        for part in parts[:-1]:
            node = node.get(part, {}).get("files", {})
        info = node.get(parts[-1])
        if not info or "offset" not in info:
            raise AsarError(f"asar 内找不到文件: {name}")
        self._f.seek(self.base_offset + int(info["offset"]))
        return self._f.read(int(info["size"]))

    def read_text(self, name):
        return self.read(name).decode("utf-8")

    def version(self):
        """读取归档内 package.json 的版本号。"""
        return json.loads(self.read_text("package.json")).get("version", "?")

    def extract_to(self, dest_dir):
        """解包全部文件到 dest_dir，返回 (文件数, 总字节数)。

        所有条目先经 _safe_parts 校验，再 resolve 并确认仍在 dest_dir
        之内（双保险，防 asar-slip 路径穿越）。
        """
        root = Path(dest_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        count = 0
        total = 0
        for name, info in self._walk():
            parts = _safe_parts(name)
            dest = root.joinpath(*parts).resolve()
            if not dest.is_relative_to(root):
                raise AsarError(f"条目越界，已拒绝: {name}")
            size = int(info["size"])
            self._f.seek(self.base_offset + int(info["offset"]))
            data = self._f.read(size)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            count += 1
            total += size
        return count, total
