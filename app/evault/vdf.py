"""Minimal Valve KeyValues readers/writers (text VDF and binary shortcuts.vdf)."""
import re
import struct


# ---------------------------------------------------------------- text VDF

_TOKEN = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])|(//[^\n]*)')


def _unescape(s):
    return s.replace('\\"', '"').replace('\\\\', '\\')


def parse_text(text):
    root = {}
    stack = [root]
    key = None
    for m in _TOKEN.finditer(text):
        if m.group(3):
            continue
        brace = m.group(2)
        if brace == '{':
            node = {}
            stack[-1][key if key is not None else ''] = node
            stack.append(node)
            key = None
        elif brace == '}':
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            val = _unescape(m.group(1))
            if key is None:
                key = val
            else:
                stack[-1][key] = val
                key = None
    return root


def load_text(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return parse_text(f.read())


# ---------------------------------------------------------------- binary VDF

class U64(int):
    pass


class F32(float):
    pass


def parse_binary(data):
    pos = 0

    def cstr():
        nonlocal pos
        end = data.index(b'\x00', pos)
        s = data[pos:end].decode('utf-8', 'replace')
        pos = end + 1
        return s

    def read_map():
        nonlocal pos
        d = {}
        while pos < len(data):
            t = data[pos]
            pos += 1
            if t == 0x08:
                return d
            k = cstr()
            if t == 0x00:
                d[k] = read_map()
            elif t == 0x01:
                d[k] = cstr()
            elif t == 0x02:
                d[k] = struct.unpack_from('<i', data, pos)[0]
                pos += 4
            elif t == 0x03:
                d[k] = F32(struct.unpack_from('<f', data, pos)[0])
                pos += 4
            elif t == 0x07:
                d[k] = U64(struct.unpack_from('<Q', data, pos)[0])
                pos += 8
            else:
                raise ValueError(f'Unknown binary VDF type 0x{t:02x} at {pos}')
        return d

    return read_map()


def dump_binary(obj):
    out = bytearray()

    def write_map(d):
        for k, v in d.items():
            kb = str(k).encode('utf-8') + b'\x00'
            if isinstance(v, dict):
                out.append(0x00)
                out.extend(kb)
                write_map(v)
                out.append(0x08)
            elif isinstance(v, U64):
                out.append(0x07)
                out.extend(kb)
                out.extend(struct.pack('<Q', int(v)))
            elif isinstance(v, F32):
                out.append(0x03)
                out.extend(kb)
                out.extend(struct.pack('<f', float(v)))
            elif isinstance(v, bool) or isinstance(v, int):
                out.append(0x02)
                out.extend(kb)
                out.extend(struct.pack('<I', int(v) & 0xFFFFFFFF))
            else:
                out.append(0x01)
                out.extend(kb)
                out.extend(str(v).encode('utf-8') + b'\x00')

    write_map(obj)
    out.append(0x08)
    return bytes(out)
