"""Small independent Compound File Binary reader/writer (MS-CFB).

No OLE/COM automation is used. The writer creates version-3 CFB containers,
including mini streams and a directory tree. The reader also accepts version 4.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

FREE = 0xFFFFFFFF
END = 0xFFFFFFFE
FAT = 0xFFFFFFFD
DIF = 0xFFFFFFFC
MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
MAX_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024


@dataclass
class Entry:
    name: str
    kind: int
    left: int = FREE
    right: int = FREE
    child: int = FREE
    start: int = END
    size: int = 0
    color: int = 1
    clsid: bytes = bytes(16)
    state_bits: int = 0
    created: int = 0
    modified: int = 0


class CompoundFile:
    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != MAGIC:
            raise ValueError("OLE/CFB 파일이 아닙니다.")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
        if self.sector_size not in (512, 4096):
            raise ValueError("지원하지 않는 CFB 섹터 크기입니다.")
        self.cutoff = struct.unpack_from("<I", data, 56)[0]
        fat_ids = list(struct.unpack_from("<109I", data, 76))
        dif_start, dif_count = struct.unpack_from("<II", data, 68)
        seen = set()
        for _ in range(dif_count):
            if dif_start >= DIF or dif_start in seen:
                raise ValueError("CFB DIFAT 체인이 손상되었습니다.")
            seen.add(dif_start)
            words = struct.unpack("<%dI" % (self.sector_size // 4), self.sector(dif_start))
            fat_ids.extend(words[:-1])
            dif_start = words[-1]
        fat_ids = [v for v in fat_ids if v < DIF]
        self.fat = []
        for sid in fat_ids:
            self.fat.extend(struct.unpack("<%dI" % (self.sector_size // 4), self.sector(sid)))
        directory = self.chain(struct.unpack_from("<I", data, 48)[0], self.fat)
        self.entries = []
        for off in range(0, len(directory), 128):
            item = directory[off:off + 128]
            if len(item) < 128:
                break
            length = min(struct.unpack_from("<H", item, 64)[0], 64)
            name = item[:max(0, length - 2)].decode("utf-16le", "replace")
            left, right, child = struct.unpack_from("<III", item, 68)
            start = struct.unpack_from("<I", item, 116)[0]
            size = struct.unpack_from("<Q", item, 120)[0]
            if self.sector_size == 512:
                size &= 0xFFFFFFFF
            state_bits, created, modified = struct.unpack_from("<IQQ", item, 96)
            self.entries.append(Entry(name, item[66], left, right, child, start, size, item[67],
                item[80:96], state_bits, created, modified))
        if not self.entries or self.entries[0].kind != 5:
            raise ValueError("CFB 루트 디렉터리가 없습니다.")
        mini_start, mini_count = struct.unpack_from("<II", data, 60)
        mini = self.chain(mini_start, self.fat, mini_count * self.sector_size) if mini_count else b""
        self.mini_fat = list(struct.unpack("<%dI" % (len(mini) // 4), mini)) if mini else []
        self.mini_stream = self.chain(self.entries[0].start, self.fat, self.entries[0].size)
        self.streams: dict[str, Entry] = {}
        self.storages: dict[str, Entry] = {"": self.entries[0]}
        self._walk(self.entries[0].child, "", set())

    def sector(self, sid: int) -> bytes:
        pos = (sid + 1) * self.sector_size
        if sid >= DIF or pos + self.sector_size > len(self.data):
            raise ValueError("CFB 섹터가 파일 범위를 벗어났습니다.")
        return self.data[pos:pos + self.sector_size]

    def chain(self, sid: int, table: list[int], size: int | None = None, mini: bool = False) -> bytes:
        if size is not None and size > MAX_BYTES:
            raise ValueError("CFB 스트림이 지원 크기를 초과했습니다.")
        result = bytearray()
        seen = set()
        unit = 64 if mini else self.sector_size
        while sid < DIF and (size is None or len(result) < size):
            if sid in seen or sid >= len(table):
                raise ValueError("CFB 체인이 손상되었습니다.")
            seen.add(sid)
            if mini:
                chunk = self.mini_stream[sid * 64:(sid + 1) * 64]
                if len(chunk) != 64:
                    raise ValueError("CFB 미니 스트림이 손상되었습니다.")
            else:
                chunk = self.sector(sid)
            result.extend(chunk)
            if len(result) > MAX_BYTES:
                raise ValueError("CFB 스트림이 지원 크기를 초과했습니다.")
            sid = table[sid]
        if size is not None and len(result) < size:
            raise ValueError("CFB 스트림 길이가 올바르지 않습니다.")
        return bytes(result[:size] if size is not None else result)

    def _walk(self, sid: int, parent: str, seen: set[int]):
        if sid == FREE:
            return
        if sid in seen or sid >= len(self.entries):
            raise ValueError("CFB 디렉터리 트리가 손상되었습니다.")
        seen.add(sid)
        e = self.entries[sid]
        self._walk(e.left, parent, seen)
        path = (parent + "/" + e.name).lstrip("/")
        if e.kind == 2:
            self.streams[path] = e
        elif e.kind == 1:
            self.storages[path] = e
            self._walk(e.child, path, seen)
        self._walk(e.right, parent, seen)

    def read(self, name: str) -> bytes:
        e = self.streams[name]
        return self.chain(e.start, self.mini_fat if e.size < self.cutoff else self.fat,
                          e.size, mini=e.size < self.cutoff)


def write_compound(streams: dict[str, bytes], template: CompoundFile | None = None) -> bytes:
    """Encode stream paths as a version-3 CFB file, with valid mini FAT."""
    entries = [Entry("Root Entry", 5)]
    children: dict[int, list[int]] = {0: []}
    storages = {"": 0}
    stream_data: dict[int, bytes] = {}
    def storage(path):
        current, parent_path = 0, ""
        for name in path.split("/") if path else []:
            parent_path = (parent_path + "/" + name).lstrip("/")
            if parent_path not in storages:
                eid = len(entries)
                entries.append(Entry(name, 1))
                children.setdefault(current, []).append(eid)
                children[eid] = []
                storages[parent_path] = eid
            current = storages[parent_path]
        return current
    # Empty storages and OLE directory metadata belong to the source file too.
    if template is not None:
        for path in template.storages:
            storage(path)
    for path, data in streams.items():
        parts = path.split("/")
        current = storage("/".join(parts[:-1]))
        eid = len(entries)
        entries.append(Entry(parts[-1], 2, size=len(data)))
        children.setdefault(current, []).append(eid)
        stream_data[eid] = data

    if template is not None:
        paths = {eid: path for path, eid in storages.items()}
        paths.update({eid: path for eid, path in zip(stream_data, streams)})
        for eid, path in paths.items():
            source = template.storages.get(path) or template.streams.get(path)
            if source is not None:
                for attribute in ("clsid", "state_bits", "created", "modified"):
                    setattr(entries[eid], attribute, getattr(source, attribute))

    # Balanced sibling trees. Nodes on the incomplete last level are red,
    # giving equal black depth as required by MS-CFB's red-black tree.
    def tree(ids: list[int], depth=0, red_depth=0):
        if not ids:
            return FREE
        mid = len(ids) // 2
        eid = ids[mid]
        entries[eid].color = 0 if depth == red_depth and depth > 0 else 1
        entries[eid].left = tree(ids[:mid], depth + 1, red_depth)
        entries[eid].right = tree(ids[mid + 1:], depth + 1, red_depth)
        return eid
    for parent, ids in children.items():
        ids.sort(key=lambda i: (len(entries[i].name), entries[i].name.upper()))
        red_depth = (len(ids) + 1).bit_length() - 1
        entries[parent].child = tree(ids, red_depth=red_depth)

    mini_data = bytearray()
    mini_fat: list[int] = []
    sectors: list[bytes] = []
    chains: list[tuple[int, int]] = []
    def add_regular(data: bytes) -> int:
        if not data:
            return END
        start = len(sectors)
        count = (len(data) + 511) // 512
        padded = data.ljust(count * 512, b"\0")
        sectors.extend(padded[i:i + 512] for i in range(0, len(padded), 512))
        chains.append((start, count))
        return start
    for eid, data in stream_data.items():
        if len(data) < 4096:
            if not data:
                entries[eid].start = END
                continue
            entries[eid].start = len(mini_fat)
            count = (len(data) + 63) // 64
            start = len(mini_fat)
            mini_fat.extend([start + i + 1 if i < count - 1 else END for i in range(count)])
            mini_data.extend(data.ljust(count * 64, b"\0"))
        else:
            entries[eid].start = add_regular(data)
    entries[0].size = len(mini_data)
    entries[0].start = add_regular(bytes(mini_data))
    mini_fat_start = END
    mini_fat_count = 0
    if mini_fat:
        mini_fat_count = (len(mini_fat) + 127) // 128
        mini_fat.extend([FREE] * (mini_fat_count * 128 - len(mini_fat)))
        mini_fat_start = add_regular(struct.pack("<%dI" % len(mini_fat), *mini_fat))

    directory = bytearray()
    for e in entries:
        encoded = e.name.encode("utf-16le")[:62] + b"\0\0"
        item = bytearray(128)
        item[:len(encoded)] = encoded
        struct.pack_into("<HBBIII", item, 64, len(encoded), e.kind, e.color, e.left, e.right, e.child)
        item[80:96] = e.clsid
        struct.pack_into("<IQQ", item, 96, e.state_bits, e.created, e.modified)
        struct.pack_into("<IQ", item, 116, e.start, e.size)
        directory.extend(item)
    directory_start = add_regular(bytes(directory))
    n_data = len(sectors)
    fat_count, difat_count = (n_data + 127) // 128, 0
    while True:
        updated_difat = max(0, (fat_count - 109 + 126) // 127)
        updated_fat = (n_data + fat_count + updated_difat + 127) // 128
        if (updated_fat, updated_difat) == (fat_count, difat_count):
            break
        fat_count, difat_count = updated_fat, updated_difat
    allocation = [FREE] * (fat_count * 128)
    for start, count in chains:
        for i in range(count):
            allocation[start + i] = start + i + 1 if i < count - 1 else END
    for sid in range(n_data, n_data + fat_count):
        allocation[sid] = FAT
    difat_start = n_data + fat_count if difat_count else END
    for sid in range(n_data + fat_count, n_data + fat_count + difat_count):
        allocation[sid] = 0xFFFFFFFC
    fat_bytes = struct.pack("<%dI" % len(allocation), *allocation)
    sectors.extend(fat_bytes[i:i + 512] for i in range(0, len(fat_bytes), 512))
    remaining_fat = list(range(n_data + 109, n_data + fat_count))
    for i in range(difat_count):
        ids = remaining_fat[i * 127:(i + 1) * 127]
        ids += [FREE] * (127 - len(ids))
        ids.append(difat_start + i + 1 if i + 1 < difat_count else END)
        sectors.append(struct.pack("<128I", *ids))
    header = bytearray(512)
    header[:8] = MAGIC
    struct.pack_into("<HHHH", header, 24, 0x003E, 3, 0xFFFE, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, fat_count, directory_start, 0, 4096,
                     mini_fat_start, mini_fat_count, difat_start, difat_count)
    difat = list(range(n_data, n_data + min(fat_count, 109))) + [FREE] * max(0, 109 - fat_count)
    struct.pack_into("<109I", header, 76, *difat)
    return bytes(header) + b"".join(sectors)
