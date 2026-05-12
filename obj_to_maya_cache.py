#!/usr/bin/env python3
"""
Convert an initial OBJ + animation OBJ sequence into a Maya geometry cache
(.xml + .mc files in IFF/FOR4 mcc format).

The initial OBJ defines topology and vertex count. Each animation OBJ must
have the same vertex count and supplies positions for one frame. The resulting
cache can be applied in Maya (Cache > Geometry Cache > Import Cache) to the
mesh imported from the initial OBJ.

Example:
    python obj_to_maya_cache.py rest.obj "anim/frame_*.obj" -o out/cloth \
        --fps 24 --start-frame 1
"""

import argparse
import glob
import os
import re
import struct
import sys

TICKS_PER_SECOND = 6000  # Maya's internal time unit


# ---------- OBJ parsing ----------

def read_obj_vertices(path):
    """Return list of (x, y, z) tuples from an OBJ's 'v' lines (ignores w, vt, vn)."""
    verts = []
    with open(path, "r") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
    return verts


# ---------- IFF FOR4 chunk helpers ----------

def _pad4(data: bytes) -> bytes:
    return data + (b"\x00" * ((-len(data)) % 4))


def _chunk(tag: bytes, data: bytes) -> bytes:
    """Simple chunk: 4-byte tag + 4-byte BE size + data, padded to 4-byte boundary."""
    assert len(tag) == 4
    return tag + struct.pack(">I", len(data)) + _pad4(data)


def _for4(group_type: bytes, contents: bytes) -> bytes:
    """FOR4 group chunk wrapping `group_type` + already-built sub-chunks."""
    assert len(group_type) == 4
    inner = group_type + contents
    return b"FOR4" + struct.pack(">I", len(inner)) + _pad4(inner)


# ---------- Maya cache (.mc) writing ----------

def _cach_header() -> bytes:
    """Top-level CACH chunk: just a header block containing VRSN/STIM/ETIM directly.
    NOT a wrapper around the per-frame MYCH chunks (those are top-level siblings).
    STIM=0 and ETIM=1 are constants that Maya itself writes; they are NOT the
    cache time range (that lives in the XML)."""
    vrsn = _chunk(b"VRSN", b"0.1\x00")
    stim = _chunk(b"STIM", struct.pack(">i", 0))
    etim = _chunk(b"ETIM", struct.pack(">i", 1))
    return _for4(b"CACH", vrsn + stim + etim)


def _frame_chunk(channel_name: str, tick: int, verts, use_double: bool) -> bytes:
    """One MYCH FOR4 chunk for one frame. Sits at top level of the file."""
    time_chk = _chunk(b"TIME", struct.pack(">i", tick))
    chnm = _chunk(b"CHNM", channel_name.encode("utf-8") + b"\x00")
    size_chk = _chunk(b"SIZE", struct.pack(">I", len(verts)))
    flat = [c for v in verts for c in v]
    if use_double:
        arr = struct.pack(">" + "d" * len(flat), *flat)
        data_chk = _chunk(b"DVCA", arr)
    else:
        arr = struct.pack(">" + "f" * len(flat), *flat)
        data_chk = _chunk(b"FVCA", arr)
    return _for4(b"MYCH", time_chk + chnm + size_chk + data_chk)


def write_one_file_cache(mc_path, channel_name, frames, ticks_per_frame, use_double):
    """frames: list of (frame_index, verts). Layout: CACH then a flat sequence of MYCH."""
    cach = _cach_header()
    body = b"".join(
        _frame_chunk(channel_name, f * ticks_per_frame, v, use_double)
        for f, v in frames
    )
    with open(mc_path, "wb") as f:
        f.write(cach + body)


def write_per_frame_cache(base_path, channel_name, frames, ticks_per_frame, use_double):
    """Writes one .mc per frame: <base>Frame<N>.mc. Same flat layout: CACH + one MYCH."""
    base_dir = os.path.dirname(base_path) or "."
    base_name = os.path.basename(base_path)
    for frame_index, verts in frames:
        tick = frame_index * ticks_per_frame
        cach = _cach_header()
        body = _frame_chunk(channel_name, tick, verts, use_double)
        out = os.path.join(base_dir, f"{base_name}Frame{frame_index}.mc")
        with open(out, "wb") as f:
            f.write(cach + body)


# ---------- XML descriptor ----------

def write_xml(xml_path, channel_name, start_frame, end_frame,
              ticks_per_frame, one_file=True):
    start_tick = start_frame * ticks_per_frame
    end_tick = end_frame * ticks_per_frame
    cache_type = "OneFile" if one_file else "OneFilePerFrame"
    xml = (
        '<?xml version="1.0"?>\n'
        '<Autodesk_Cache_File>\n'
        f'  <cacheType Type="{cache_type}" Format="mcc"/>\n'
        f'  <time Range="{start_tick}-{end_tick}"/>\n'
        f'  <cacheTimePerFrame TimePerFrame="{ticks_per_frame}"/>\n'
        '  <cacheVersion Version="2.0"/>\n'
        '  <Channels>\n'
        f'    <channel0 ChannelName="{channel_name}" '
        'ChannelType="FloatVectorArray" '
        'ChannelInterpretation="positions" '
        'SamplingType="Regular" '
        f'SamplingRate="{ticks_per_frame}" '
        f'StartTime="{start_tick}" EndTime="{end_tick}"/>\n'
        '  </Channels>\n'
        '</Autodesk_Cache_File>\n'
    )
    with open(xml_path, "w") as f:
        f.write(xml)


# ---------- Driver ----------

def _natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def gather_anim_files(pattern):
    files = sorted(glob.glob(pattern), key=_natural_key)
    if not files:
        raise FileNotFoundError(f"No files matched pattern: {pattern}")
    return files


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("initial_obj", help="Initial / rest-pose OBJ. Defines topology.")
    ap.add_argument("anim_pattern",
                    help="Glob for animation OBJs, e.g. 'anim/frame_*.obj' "
                         "(quote it to prevent shell expansion).")
    ap.add_argument("-o", "--out", required=True,
                    help="Output cache base path (no extension). "
                         "Creates <out>.xml and <out>.mc (or per-frame .mc files).")
    ap.add_argument("--channel", default=None,
                    help="Cache channel name. Defaults to basename(out)+'Shape'. "
                         "For convenience match this to the Maya shape node name.")
    ap.add_argument("--fps", type=float, default=24.0,
                    help="Frame rate (default 24). Determines ticks per frame.")
    ap.add_argument("--start-frame", type=int, default=1,
                    help="Frame number assigned to the first animation OBJ (default 1).")
    ap.add_argument("--include-initial", action="store_true",
                    help="Also write the initial OBJ as frame (start-frame - 1).")
    ap.add_argument("--double", action="store_true",
                    help="Store positions as double (DVCA) instead of float (FVCA).")
    ap.add_argument("--per-frame", action="store_true",
                    help="Write one .mc per frame (OneFilePerFrame).")
    args = ap.parse_args()

    ticks_per_frame = int(round(TICKS_PER_SECOND / args.fps))
    if abs(TICKS_PER_SECOND / args.fps - ticks_per_frame) > 1e-6:
        print(f"[warn] fps={args.fps} does not divide {TICKS_PER_SECOND} cleanly; "
              f"rounded TimePerFrame={ticks_per_frame}.", file=sys.stderr)

    init_verts = read_obj_vertices(args.initial_obj)
    if not init_verts:
        sys.exit(f"No vertices found in {args.initial_obj}")
    n_verts = len(init_verts)
    print(f"[init] {args.initial_obj}: {n_verts} vertices")

    anim_files = gather_anim_files(args.anim_pattern)
    print(f"[anim] {len(anim_files)} files matched '{args.anim_pattern}'")

    frames = []
    if args.include_initial:
        frames.append((args.start_frame - 1, init_verts))
    for i, path in enumerate(anim_files):
        v = read_obj_vertices(path)
        if len(v) != n_verts:
            sys.exit(f"Vertex count mismatch in {path}: got {len(v)}, "
                     f"expected {n_verts} (from {args.initial_obj}).")
        frames.append((args.start_frame + i, v))

    start_frame = frames[0][0]
    end_frame = frames[-1][0]

    out_base = args.out
    out_dir = os.path.dirname(out_base) or "."
    os.makedirs(out_dir, exist_ok=True)
    channel_name = args.channel or (os.path.basename(out_base) + "Shape")
    xml_path = out_base + ".xml"

    if args.per_frame:
        write_per_frame_cache(out_base, channel_name, frames,
                              ticks_per_frame, args.double)
        print(f"[out] {len(frames)} per-frame .mc files at "
              f"{out_base}Frame<N>.mc")
    else:
        mc_path = out_base + ".mc"
        write_one_file_cache(mc_path, channel_name, frames,
                             ticks_per_frame, args.double)
        size_mb = os.path.getsize(mc_path) / (1024 * 1024)
        print(f"[out] {mc_path} ({size_mb:.2f} MB)")

    write_xml(xml_path, channel_name, start_frame, end_frame,
              ticks_per_frame, one_file=not args.per_frame)
    print(f"[out] {xml_path}")
    print(f"[done] channel='{channel_name}', frames {start_frame}-{end_frame}, "
          f"{n_verts} verts, {args.fps} fps")


if __name__ == "__main__":
    main()