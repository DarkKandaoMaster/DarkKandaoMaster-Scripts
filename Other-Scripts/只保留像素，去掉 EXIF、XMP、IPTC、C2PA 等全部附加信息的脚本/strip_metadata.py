#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
无损剥离图片元数据（像素零损失）。

- PNG/APNG：chunk 级重写，只保留 IHDR/PLTE/tRNS/IDAT/IEND（APNG 另留 acTL/fcTL/fdAT），
  图像数据字节与 CRC 原样拷贝，不解码不重编码。
- JPEG：段级剥离 APPn/COM（Adobe APP14 因影响颜色解码而保留），压缩数据原样拷贝。
- WebP：RIFF chunk 级剥离，只留 VP8X/VP8/VP8L/ALPH/ANIM/ANMF，并清除 VP8X 中的 ICC/EXIF/XMP 标志位。
- 其他格式：报错退出，绝不做有损重编码。

处理后自动验证：解码前后像素完全一致 + 输出文件只剩允许的结构块；不通过则删除输出并报错。

用法：
  python strip_metadata.py 输入 [输入...] [-o 输出文件或目录] [--inplace] [-r] [-f]
"""
import argparse
import os
import struct
import sys
import tempfile
import zlib

import numpy as np

try:
    import cv2
except ImportError:  # cv2 缺失时退化为仅用 PIL 验证
    cv2 = None
try:
    from PIL import Image, ImageSequence
except ImportError:
    Image = None

# 防止控制台编码导致打印崩溃
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass


class StripError(Exception):
    pass


# 颜色相关的元数据：剥离后部分软件显示颜色可能略变，但像素值不变
COLOR_CHUNKS = {"iCCP", "sRGB", "gAMA", "cHRM", "cICP", "mDCv", "cLLi", "sBIT",
                "ICCP", "APP2(ICC_PROFILE)"}


# ---------------------------------------------------------------- 格式识别
def detect_format(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


# ---------------------------------------------------------------- PNG
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_KEEP = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}
APNG_KEEP = {b"acTL", b"fcTL", b"fdAT"}
PNG_DATA = {b"IDAT", b"fdAT", b"fcTL", b"acTL"}  # 验证时逐字节比对的块


def png_chunks(data):
    """解析 PNG，返回 [(类型, 原始完整块字节, 数据长度)]，同时校验 CRC；另返回 IEND 后多余字节数。"""
    if data[:8] != PNG_SIG:
        raise StripError("不是 PNG 文件")
    pos, out = 8, []
    while True:
        if pos + 12 > len(data):
            raise StripError("PNG 截断：缺少 IEND")
        length, = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise StripError(f"PNG 截断：{ctype!r} 块不完整")
        crc, = struct.unpack(">I", data[end - 4:end])
        if zlib.crc32(data[pos + 4:end - 4]) & 0xFFFFFFFF != crc:
            raise StripError(f"PNG 块 {ctype.decode('latin1')} CRC 校验失败，文件可能已损坏")
        out.append((ctype, data[pos:end], length))
        pos = end
        if ctype == b"IEND":
            break
    return out, len(data) - pos


def strip_png(data):
    chunks, trailing = png_chunks(data)
    if chunks[0][0] != b"IHDR":
        raise StripError("PNG 首块不是 IHDR")
    is_apng = any(c[0] == b"acTL" for c in chunks)
    keep = PNG_KEEP | (APNG_KEEP if is_apng else set())
    out, removed = [PNG_SIG], []
    for ctype, raw, length in chunks:
        name = ctype.decode("latin1")
        if ctype in keep:
            out.append(raw)  # 原样拷贝（含 CRC）
        elif ctype[0:1].isupper() and ctype not in APNG_KEEP:
            # 未知关键块（如 Apple CgBI）丢弃会导致无法解码，直接拒绝
            raise StripError(f"遇到未知关键块 {name}，无法安全处理")
        else:
            removed.append((name, length))
    if trailing:
        removed.append(("<IEND 之后的附加数据>", trailing))
    info = {"format": "APNG" if is_apng else "PNG", "chunks": [(c[0].decode("latin1"), c[2]) for c in chunks],
            "allowed": {k.decode() for k in keep}}
    return b"".join(out), removed, info


def check_png_output(orig, new, allowed):
    """输出只含允许块，且图像数据块与原图逐字节一致。"""
    nc, trailing = png_chunks(new)
    bad = [c[0].decode("latin1") for c in nc if c[0].decode("latin1") not in allowed]
    if bad or trailing:
        raise StripError(f"输出仍含不允许的块：{bad} / 尾部字节 {trailing}")
    oc, _ = png_chunks(orig)
    if [c[1] for c in oc if c[0] in PNG_DATA | {b"IHDR", b"PLTE", b"tRNS"}] != \
       [c[1] for c in nc if c[0] in PNG_DATA | {b"IHDR", b"PLTE", b"tRNS"}]:
        raise StripError("输出的图像数据块与原图不一致")
    return [c[0].decode("latin1") for c in nc]


# ---------------------------------------------------------------- JPEG
# 无长度字段的标记：SOI、EOI、RST0-7、TEM
JPEG_NOLEN = {0xD8, 0xD9, 0x01} | set(range(0xD0, 0xD8))


def jpeg_segments(data):
    """解析 JPEG，返回 [(标记, 原始字节)]；SOS 段含其后的熵编码数据。另返回 EOI 后多余字节数。"""
    if data[:2] != b"\xff\xd8":
        raise StripError("不是 JPEG 文件")
    pos, segs = 2, [(0xD8, b"\xff\xd8")]
    n = len(data)
    while pos < n:
        if data[pos] != 0xFF:
            raise StripError(f"JPEG 结构异常：偏移 {pos} 处不是标记")
        while pos < n and data[pos] == 0xFF:  # 跳过填充 FF
            pos += 1
        if pos >= n:
            raise StripError("JPEG 截断")
        marker = data[pos]
        start = pos - 1
        pos += 1
        if marker == 0xD9:  # EOI
            segs.append((marker, b"\xff\xd9"))
            return segs, n - pos
        if marker in JPEG_NOLEN:
            segs.append((marker, data[start:pos]))
            continue
        if pos + 2 > n:
            raise StripError("JPEG 截断")
        length, = struct.unpack(">H", data[pos:pos + 2])
        end = pos + length
        if end > n:
            raise StripError(f"JPEG 段 FF{marker:02X} 不完整")
        if marker == 0xDA:  # SOS：继续扫描熵编码数据直到下一个非 RST 标记
            p = end
            while True:
                p = data.find(b"\xff", p)
                if p < 0 or p + 1 >= n:
                    raise StripError("JPEG 截断：扫描数据缺少结束标记")
                nxt = data[p + 1]
                if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:  # 字节填充或 RST，属于扫描数据
                    p += 2
                    continue
                if nxt == 0xFF:  # 连续 FF 填充，继续判断下一个
                    p += 1
                    continue
                break
            end = p
        segs.append((marker, data[start:end]))
        pos = end
    raise StripError("JPEG 截断：缺少 EOI")


def jpeg_name(marker, raw):
    if 0xE0 <= marker <= 0xEF:
        payload = raw[4:40]
        tag = payload.split(b"\x00")[0].decode("latin1", "replace")[:20]
        return f"APP{marker - 0xE0}({tag})"
    if marker == 0xFE:
        return "COM"
    names = {0xD8: "SOI", 0xD9: "EOI", 0xDA: "SOS", 0xDB: "DQT", 0xC4: "DHT", 0xDD: "DRI", 0xCC: "DAC", 0xDC: "DNL"}
    if marker in names:
        return names[marker]
    if 0xC0 <= marker <= 0xCF:
        return f"SOF{marker - 0xC0}"
    if 0xD0 <= marker <= 0xD7:
        return f"RST{marker - 0xD0}"
    return f"FF{marker:02X}"


def jpeg_is_metadata(marker, raw):
    """APPn 与 COM 视为元数据；Adobe APP14 决定颜色变换（影响解码像素），保留。"""
    if marker == 0xEE and raw[4:9] == b"Adobe":
        return False
    return 0xE0 <= marker <= 0xEF or marker == 0xFE


def strip_jpeg(data):
    segs, trailing = jpeg_segments(data)
    out, removed = [], []
    for marker, raw in segs:
        if jpeg_is_metadata(marker, raw):
            removed.append((jpeg_name(marker, raw), len(raw)))
        else:
            out.append(raw)
    if trailing:
        removed.append(("<EOI 之后的附加数据（如 MPF 副图/缩略图）>", trailing))
    info = {"format": "JPEG", "chunks": [(jpeg_name(m, r), len(r)) for m, r in segs]}
    return b"".join(out), removed, info


def check_jpeg_output(orig, new):
    segs, trailing = jpeg_segments(new)
    bad = [jpeg_name(m, r) for m, r in segs if jpeg_is_metadata(m, r)]
    if bad or trailing:
        raise StripError(f"输出仍含元数据段：{bad} / 尾部字节 {trailing}")
    osegs, _ = jpeg_segments(orig)
    if [r for m, r in osegs if not jpeg_is_metadata(m, r)] != [r for m, r in segs]:
        raise StripError("输出的图像数据段与原图不一致")
    return [jpeg_name(m, r) for m, r in segs]


# ---------------------------------------------------------------- WebP
WEBP_KEEP = {b"VP8X", b"VP8 ", b"VP8L", b"ALPH", b"ANIM", b"ANMF"}
ANMF_KEEP = {b"ALPH", b"VP8 ", b"VP8L"}


def riff_chunks(data, start, end):
    """解析 RIFF 子块，返回 [(fourcc, payload)]。"""
    pos, out = start, []
    while pos < end:
        if pos + 8 > end:
            raise StripError("WebP 截断")
        fourcc = data[pos:pos + 4]
        size, = struct.unpack("<I", data[pos + 4:pos + 8])
        if pos + 8 + size > end:
            raise StripError(f"WebP 块 {fourcc!r} 不完整")
        out.append((fourcc, data[pos + 8:pos + 8 + size]))
        pos += 8 + size + (size & 1)  # 奇数长度有 1 字节填充
    return out


def riff_pack(fourcc, payload):
    return fourcc + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def webp_parse(data):
    if not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise StripError("不是 WebP 文件")
    riff_size, = struct.unpack("<I", data[4:8])
    end = 8 + riff_size
    if end > len(data):
        raise StripError("WebP 截断")
    return riff_chunks(data, 12, end), len(data) - end


def strip_webp(data):
    chunks, trailing = webp_parse(data)
    out, removed = [], []
    for fourcc, payload in chunks:
        name = fourcc.decode("latin1")
        if fourcc not in WEBP_KEEP:
            removed.append((name, len(payload)))
            continue
        if fourcc == b"VP8X":
            # 清除 ICC(0x20)/EXIF(0x08)/XMP(0x04) 标志位，保留 Alpha/动画
            payload = bytes([payload[0] & ~0x2C & 0xFF]) + payload[1:]
        elif fourcc == b"ANMF":
            # 帧内只保留图像数据子块
            sub = riff_chunks(payload, 16, len(payload))
            kept = [s for s in sub if s[0] in ANMF_KEEP]
            for s in sub:
                if s[0] not in ANMF_KEEP:
                    removed.append((f"ANMF/{s[0].decode('latin1')}", len(s[1])))
            payload = payload[:16] + b"".join(riff_pack(f, p) for f, p in kept)
        out.append(riff_pack(fourcc, payload))
    if trailing:
        removed.append(("<RIFF 之后的附加数据>", trailing))
    body = b"WEBP" + b"".join(out)
    info = {"format": "WebP", "chunks": [(c[0].decode("latin1"), len(c[1])) for c in chunks]}
    return b"RIFF" + struct.pack("<I", len(body)) + body, removed, info


def check_webp_output(orig, new):
    nc, trailing = webp_parse(new)
    bad = [c[0].decode("latin1") for c in nc if c[0] not in WEBP_KEEP]
    for f, p in nc:
        if f == b"VP8X" and p[0] & 0x2C:
            bad.append("VP8X 元数据标志位")
        if f == b"ANMF":
            bad += [f"ANMF/{s[0].decode('latin1')}" for s in riff_chunks(p, 16, len(p)) if s[0] not in ANMF_KEEP]
    if bad or trailing:
        raise StripError(f"输出仍含不允许的块：{bad} / 尾部字节 {trailing}")
    oc, _ = webp_parse(orig)
    img = {b"VP8 ", b"VP8L", b"ALPH"}
    if [p for f, p in oc if f in img] != [p for f, p in nc if f in img]:
        raise StripError("输出的图像数据块与原图不一致")
    return [c[0].decode("latin1") for c in nc]


# ---------------------------------------------------------------- 像素验证
def decode_cv2(data):
    """cv2 以 IMREAD_UNCHANGED 解码（保留 16 位与 Alpha，不做 EXIF 旋转）；用 imdecode 规避非 ASCII 路径问题。"""
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise StripError("cv2 解码失败")
    return arr


def decode_pil_frames(path):
    """PIL 逐帧解码（用于动图逐帧比对），返回 (模式, 尺寸, [帧数组])。"""
    with Image.open(path) as im:
        mode, size = im.mode, im.size
        frames = [np.array(f.copy()) for f in ImageSequence.Iterator(im)]
    return mode, size, frames


def verify_pixels(src_path, orig, out_path, new):
    lines = []
    if cv2 is not None:
        a, b = decode_cv2(orig), decode_cv2(new)
        ch = lambda x: 1 if x.ndim == 2 else x.shape[2]
        lines.append(f"cv2: 尺寸 {a.shape[1]}x{a.shape[0]} / {b.shape[1]}x{b.shape[0]}，"
                     f"位深 {a.dtype} / {b.dtype}，通道 {ch(a)} / {ch(b)}")
        if a.shape != b.shape or a.dtype != b.dtype or not np.array_equal(a, b):
            raise StripError("cv2 像素比较不一致")
        lines.append(f"cv2: 像素数组完全相等（{a.size} 个值）")
    if Image is not None:
        ma, sa, fa = decode_pil_frames(src_path)
        mb, sb, fb = decode_pil_frames(out_path)
        lines.append(f"PIL: 模式 {ma} / {mb}，尺寸 {sa} / {sb}，帧数 {len(fa)} / {len(fb)}")
        if ma != mb or sa != sb or len(fa) != len(fb) or \
           any(x.shape != y.shape or x.dtype != y.dtype or not np.array_equal(x, y) for x, y in zip(fa, fb)):
            raise StripError("PIL 像素比较不一致")
        lines.append("PIL: 所有帧像素数组完全相等")
    if cv2 is None and Image is None:
        raise StripError("没有可用的解码库（cv2 / PIL），无法验证，拒绝输出")
    return lines


# ---------------------------------------------------------------- 主流程
STRIPPERS = {"png": strip_png, "jpeg": strip_jpeg, "webp": strip_webp}
EXPECT_EXT = {"png": {".png", ".apng"}, "jpeg": {".jpg", ".jpeg", ".jpe", ".jfif"}, "webp": {".webp"}}


def fmt_size(n):
    return f"{n:,} 字节 ({n / 1024 / 1024:.2f} MiB)" if n >= 1024 * 1024 else f"{n:,} 字节"


def process(src, dst, inplace, force):
    with open(src, "rb") as f:
        orig = f.read()
    kind = detect_format(orig)
    if kind is None:
        raise StripError(f"不支持的格式（文件头 {orig[:12].hex()}），不做任何处理")
    ext = os.path.splitext(src)[1].lower()
    print(f"\n== {src}")
    print(f"真实格式：{kind.upper()}（按文件头识别）")
    if ext not in EXPECT_EXT[kind]:
        print(f"[警告] 扩展名 {ext} 与真实格式 {kind.upper()} 不符，输出沿用原扩展名")

    new, removed, info = STRIPPERS[kind](orig)
    print("原始结构：" + ", ".join(f"{n}({s})" for n, s in info["chunks"]))

    if not removed:
        print("未发现元数据，无需处理")
        return
    if not inplace and os.path.exists(dst) and not force:
        raise StripError(f"输出文件已存在：{dst}（加 -f 覆盖）")

    # 先写临时文件（与目标同目录），验证通过后再替换到位
    d = os.path.dirname(os.path.abspath(dst))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(dst)[1], prefix=".strip_", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new)
        # 结构复查：只剩允许的块，且图像数据逐字节一致
        if kind == "png":
            kept = check_png_output(orig, new, info["allowed"])
        elif kind == "jpeg":
            kept = check_jpeg_output(orig, new)
        else:
            kept = check_webp_output(orig, new)
        vlines = verify_pixels(src, orig, tmp, new)
        os.replace(tmp, dst)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)  # 验证未通过的输出不保留
        raise

    color = [n for n, _ in removed if n in COLOR_CHUNKS or n.startswith("APP2(ICC")]
    print("已移除：" + ", ".join(f"{n}({s})" for n, s in removed))
    print("输出结构：" + ", ".join(kept) + "（复查通过，仅剩允许的块）")
    for line in vlines:
        print("验证 " + line)
    if color:
        print(f"[注意] 移除了颜色相关信息 {color}：部分软件显示颜色可能略变，但像素值不变")
    print(f"大小：{fmt_size(len(orig))} -> {fmt_size(len(new))}（减少 {fmt_size(len(orig) - len(new))}）")
    print(f"输出：{dst}" + ("（已原地覆盖）" if inplace else ""))


def collect(inputs, recursive):
    files = []
    for p in inputs:
        if os.path.isdir(p):
            walker = os.walk(p) if recursive else [(p, [], os.listdir(p))]
            for root, _, names in walker:
                for n in sorted(names):
                    fp = os.path.join(root, n)
                    if not os.path.isfile(fp) or os.path.splitext(n)[0].endswith("_clean"):
                        continue
                    with open(fp, "rb") as f:
                        if detect_format(f.read(16)):
                            files.append(fp)
        elif os.path.isfile(p):
            files.append(p)
        else:
            raise StripError(f"找不到输入：{p}")
    return files


def main():
    ap = argparse.ArgumentParser(description="无损剥离图片元数据（PNG/APNG/JPEG/WebP），像素零损失")
    ap.add_argument("inputs", nargs="+", help="输入文件或目录")
    ap.add_argument("-o", "--output", help="输出文件路径（单文件时）或输出目录")
    ap.add_argument("--inplace", action="store_true", help="原地覆盖原图（先写临时文件，验证通过后替换）")
    ap.add_argument("-r", "--recursive", action="store_true", help="目录输入时递归子目录")
    ap.add_argument("-f", "--force", action="store_true", help="允许覆盖已存在的输出文件")
    args = ap.parse_args()

    if args.inplace and args.output:
        ap.error("--inplace 与 -o 不能同时使用")
    try:
        files = collect(args.inputs, args.recursive)
    except StripError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2
    if not files:
        print("[错误] 没有找到可处理的图片", file=sys.stderr)
        return 2
    # 多个文件或 -o 是已存在目录/以分隔符结尾时，-o 视为目录
    out_is_dir = bool(args.output) and (len(files) > 1 or os.path.isdir(args.output)
                                        or args.output.endswith(("/", "\\")))

    failed = 0
    for src in files:
        stem, ext = os.path.splitext(os.path.basename(src))
        if args.inplace:
            dst = src
        elif args.output and not out_is_dir:
            dst = args.output
        else:
            dst = os.path.join(args.output if out_is_dir else os.path.dirname(os.path.abspath(src)),
                               f"{stem}_clean{ext}")
        try:
            if not args.inplace and os.path.abspath(dst) == os.path.abspath(src):
                raise StripError("输出路径与输入相同，如需覆盖请用 --inplace")
            process(src, dst, args.inplace, args.force)
        except StripError as e:
            failed += 1
            print(f"[错误] {src}: {e}", file=sys.stderr)
    print(f"\n完成：{len(files) - failed} 成功，{failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
