#!/usr/bin/env python3
"""Verify stripping preserves ELF64 load segments and report actual sizes."""

import argparse
from pathlib import Path
import struct


def load_image(path):
    data = path.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError(f"{path}: expected a little-endian ELF64 image")
    header = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
    elf_type, machine, _, entry, phoff = header[:5]
    phentsize, phnum = header[8:10]
    # Relocatable arm64 kernels use ET_DYN; x86 boot ELFs use ET_EXEC.
    if (machine not in (62, 183) or elf_type not in (2, 3) or
            (machine == 62 and elf_type != 2) or phentsize != 56):
        raise ValueError(f"{path}: unexpected ELF type, machine or program headers")
    segments = []
    for index in range(phnum):
        kind, flags, offset, vaddr, paddr, filesz, memsz, align = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + index * phentsize)
        if kind != 1:  # PT_LOAD: what the VMM loads into guest memory.
            continue
        if filesz > memsz or offset + filesz > len(data):
            raise ValueError(f"{path}: invalid load segment")
        segments.append((flags, vaddr, paddr, memsz, align, data[offset:offset + filesz]))
    if not segments:
        raise ValueError(f"{path}: no load segments")
    return (machine, entry, segments, elf_type), len(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("stripped", type=Path)
    parser.add_argument("--arm64-image", type=Path)
    args = parser.parse_args()
    original, original_size = load_image(args.original)
    stripped, stripped_size = load_image(args.stripped)
    if original != stripped:
        raise SystemExit("Stripping changed the entry point, architecture or load segments")
    if stripped_size > original_size:
        raise SystemExit("Stripping increased the file size")
    if args.arm64_image:
        image = args.arm64_image.read_bytes()
        if original[0] != 183 or len(image) < 64 or image[56:60] != b"ARM\x64":
            raise SystemExit("Expected a bootable ARM64 Image header")
        image_size = struct.unpack_from("<Q", image, 16)[0]
        if image_size < len(image):
            raise SystemExit("ARM64 Image memory size is smaller than its file size")
        print(f"ARM64 Image: {len(image)} bytes; header memory size: {image_size} bytes")
    segments = stripped[2]
    print(f"ELF load segments unchanged; file: {original_size} -> {stripped_size} bytes")
    print(f"PT_LOAD file bytes: {sum(len(s[5]) for s in segments)}; "
          f"PT_LOAD memory bytes (includes BSS): {sum(s[3] for s in segments)}")


if __name__ == "__main__":
    main()
