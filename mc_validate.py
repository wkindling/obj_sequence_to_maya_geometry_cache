#!/usr/bin/env python3
"""Parse and dump the IFF FOR4 structure of a Maya .mc geometry cache file.

Usage:
    python mc_validate.py cloth.mc
"""

import struct
import sys


def fourcc(b):
    try:
        return b.decode("ascii")
    except UnicodeDecodeError:
        return repr(b)


def dump(buf, offset, end, depth=0, mych_seen=[0], max_mych=2):
    indent = "  " * depth
    while offset < end:
        if offset + 8 > end:
            print(f"{indent}!! truncated at offset {offset} (need 8 bytes for header)")
            return
        tag = buf[offset:offset + 4]
        size = struct.unpack(">I", buf[offset + 4:offset + 8])[0]
        data_start = offset + 8
        data_end = data_start + size
        if data_end > end:
            print(f"{indent}!! {fourcc(tag)} at {offset} declares size={size}, "
                  f"runs past EOF (end={end})")
            return

        if tag == b"FOR4":
            inner_type = buf[data_start:data_start + 4]
            if inner_type == b"MYCH":
                mych_seen[0] += 1
                if mych_seen[0] > max_mych:
                    if mych_seen[0] == max_mych + 1:
                        print(f"{indent}FOR4 MYCH (size={size}) ... "
                              f"[suppressing remaining MYCH chunks]")
                else:
                    print(f"{indent}FOR4 {fourcc(inner_type)} (size={size}, off={offset}):")
                    dump(buf, data_start + 4, data_end, depth + 1, mych_seen, max_mych)
            else:
                print(f"{indent}FOR4 {fourcc(inner_type)} (size={size}, off={offset}):")
                dump(buf, data_start + 4, data_end, depth + 1, mych_seen, max_mych)
        elif tag in (b"VRSN", b"CHNM"):
            print(f"{indent}{fourcc(tag)} (size={size}): {bytes(buf[data_start:data_end])!r}")
        elif tag in (b"STIM", b"ETIM", b"TIME"):
            if size == 4:
                v = struct.unpack(">i", buf[data_start:data_end])[0]
                print(f"{indent}{fourcc(tag)} (size=4, int32): {v}")
            elif size == 8:
                v = struct.unpack(">q", buf[data_start:data_end])[0]
                print(f"{indent}{fourcc(tag)} (size=8, int64): {v}")
            else:
                print(f"{indent}{fourcc(tag)} (size={size}, UNEXPECTED): "
                      f"{buf[data_start:data_end].hex()}")
        elif tag == b"SIZE":
            if size == 4:
                v = struct.unpack(">I", buf[data_start:data_end])[0]
                print(f"{indent}SIZE (size=4): {v}")
            else:
                print(f"{indent}SIZE (size={size}, UNEXPECTED): "
                      f"{buf[data_start:data_end].hex()}")
        elif tag in (b"FVCA", b"DVCA", b"FBCA", b"DBLA"):
            elt = 4 if tag in (b"FVCA", b"FBCA") else 8
            n = size // elt
            n_vec = n // 3 if tag in (b"FVCA", b"DVCA") else n
            head = buf[data_start:data_start + min(24, size)].hex()
            print(f"{indent}{fourcc(tag)} (size={size}, ~{n_vec} vec3, head={head})")
        else:
            print(f"{indent}{fourcc(tag)} (size={size}, UNKNOWN TAG)")

        offset = data_end + ((-size) % 4)


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <cache.mc>")
        sys.exit(1)
    with open(sys.argv[1], "rb") as f:
        buf = f.read()
    print(f"File: {sys.argv[1]}")
    print(f"Size: {len(buf)} bytes")
    print()
    dump(buf, 0, len(buf))


if __name__ == "__main__":
    main()