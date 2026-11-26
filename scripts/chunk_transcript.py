#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转写稿分块：只在说话人轮次边界切，超长单轮按句切并重挂说话人前缀。

用途
    长转写稿（1 小时以上 / 千段级）一次性喂给模型会丢细节：人名前后不一致、
    后段决议覆盖前段、数字被静默改写。本脚本把稿子切成带说话人归属的块，
    每块可独立提炼，归并时能核对得上。切法规则见 references/long-transcript.md。

跑法
    python chunk_transcript.py 会议原文.txt
    python chunk_transcript.py 会议原文.txt --budget 8000 --out-dir .tmp/chunks
    python chunk_transcript.py 会议原文.json --overlap 0.05
    python chunk_transcript.py 会议原文.txt --check      # 只报告要不要切，不写文件

判据
    · 只在「上一个人说完、下一个人开口」处切——切在句中会让半句话失去归属
    · 单轮超过预算时按句切开，每片重新挂「说话人：」前缀
    · 相邻块共享约 5% 重叠，避免跨块议题被切成两半
    · 稿子低于预算时只产出一块（等于不切），不必先判断要不要分

依赖
    仅标准库。.md/.txt 按 UTF-8/GBK/UTF-16 探测，.srt/.vtt/.json 复用
    validate_minutes.py 的解析器，保证切块与校验读到同一份文本。

退出码
    0 成功（含「不需要切」）/ 2 读取或编码失败
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 复用校验器的解析设施：两套解析器必然漂移，切块与校验必须读同一份文本
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_minutes as V  # noqa: E402

# 一行转写稿的形态：`[mm:ss] 说话人N：文本` 或 `[mm:ss] 张三：文本`
LINE_RE = re.compile(r"^\[(\d{1,3}):(\d{2})(?:-\d{1,3}:\d{2})?\]\s*([^：:\n]{1,20})[：:]\s*(.*)$")
SENT_RE = re.compile(r"(?<=[。！？!?；;])\s*")


def load_lines(path):
    """读入转写稿，返回 [(秒数, 说话人, 文本)]。

    说话人标签取不到时留空字符串——不臆造标签，切片时按「无标签」处理。
    """
    text = V.blocks_to_text(V.parse_input(path))
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if m:
            out.append((int(m.group(1)) * 60 + int(m.group(2)),
                        m.group(3).strip(), m.group(4).strip()))
        else:
            # 没有时间戳前缀的行（续行/无标签稿）挂在上一条后面，保持归属
            if out:
                base = out[-1]
                out[-1] = (base[0], base[1], (base[2] + " " + line).strip())
            else:
                out.append((0, "", line))
    return out


def split_long_turn(sec, speaker, text, max_chars):
    """把超长单轮按句切开，每片都重挂说话人前缀（关键：片片有主）。"""
    parts = [s.strip() for s in SENT_RE.split(text) if s.strip()]
    chunks, cur = [], ""
    for part in parts:
        if cur and len(cur) + len(part) > max_chars:
            chunks.append(cur.strip())
            cur = part
        else:
            cur = (cur + " " + part).strip()
    if cur:
        chunks.append(cur)
    return [(sec, speaker, c) for c in chunks]


def build_chunks(lines, budget, overlap_ratio):
    """按说话人轮次边界切块，返回 [[(秒, 说话人, 文本)]...]。

    预算按字符估（中文约 1 字符 ≈ 1 token 量级，留保守余量），不按
    行数——一行的长度差异可以很大。
    """
    # 先按轮次聚成 turn，超长轮次再拆
    turns, cur_spk, cur_sec, buf = [], None, 0, []
    for sec, spk, text in lines:
        if cur_spk is None:
            cur_spk, cur_sec = spk, sec
        if spk != cur_spk:
            turns.append((cur_sec, cur_spk, " ".join(buf).strip()))
            cur_spk, cur_sec, buf = spk, sec, [text]
        else:
            buf.append(text)
    if cur_spk is not None:
        turns.append((cur_sec, cur_spk, " ".join(buf).strip()))

    flat = []
    for sec, spk, text in turns:
        if len(text) > budget:
            flat.extend(split_long_turn(sec, spk, text, budget))
        else:
            flat.append((sec, spk, text))

    # 按预算装箱，只在 turn 边界切
    chunks, cur, size = [], [], 0
    for item in flat:
        item_len = len(item[2])
        if cur and size + item_len > budget:
            chunks.append(cur)
            # 重叠：把上一块末尾若干条带进下一块，避免跨块议题断掉
            keep, keep_size = [], 0
            for prev in reversed(cur):
                if keep_size >= int(budget * overlap_ratio):
                    break
                keep.insert(0, prev)
                keep_size += len(prev[2])
            cur, size = keep, keep_size
        cur.append(item)
        size += item_len
    if cur:
        chunks.append(cur)
    return chunks


def render(chunk):
    """把一块渲染成带统一前缀的文本，每行都是 `[mm:ss] 说话人：文本`。"""
    lines = []
    for sec, spk, text in chunk:
        stamp = f"[{sec // 60:02d}:{sec % 60:02d}]"
        lines.append(f"{stamp} {spk}：{text}" if spk else f"{stamp} {text}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="按说话人轮次边界切分长转写稿")
    ap.add_argument("file", help="转写稿（.md/.txt/.srt/.vtt/.json）")
    ap.add_argument("--budget", type=int, default=8000,
                    help="单块字符预算（默认 8000，约等于一次舒适阅读量）")
    ap.add_argument("--overlap", type=float, default=0.05,
                    help="相邻块重叠比例（默认 0.05）")
    ap.add_argument("--out-dir", default=None,
                    help="输出目录（默认 .tmp/chunks）")
    ap.add_argument("--check", action="store_true",
                    help="只报告是否需要切分，不写文件")
    ns = ap.parse_args(argv)

    path = Path(ns.file)
    if not path.is_file():
        print(f"文件不存在：{path}")
        return 2
    try:
        lines = load_lines(path)
    except (ValueError, OSError) as e:
        print(f"读取失败：{e}")
        return 2
    if not lines:
        print("转写稿为空，无可切分内容")
        return 2

    total = sum(len(t) for _, _, t in lines)
    chunks = build_chunks(lines, ns.budget, ns.overlap)

    if ns.check:
        need = len(chunks) > 1
        print(f"总字符 {total}，预算 {ns.budget}，"
              f"{'需要分块' if need else '不需要分块'}（{len(chunks)} 块）")
        return 0
    if len(chunks) <= 1:
        print(f"总字符 {total} 未超预算 {ns.budget}，不需要分块")

    out_dir = Path(ns.out_dir or ".tmp/chunks")
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, chunk in enumerate(chunks, 1):
        body = render(chunk)
        head = (f"<!-- 块 {i}/{len(chunks)}  "
                f"覆盖 {chunk[0][0] // 60:02d}:{chunk[0][0] % 60:02d}"
                f"–{chunk[-1][0] // 60:02d}:{chunk[-1][0] % 60:02d}  "
                f"说话人：{'、'.join(sorted({c[1] for c in chunk if c[1]}))} -->\n")
        (out_dir / f"chunk-{i:02d}.md").write_text(head + body + "\n", encoding="utf-8")
    print(f"已切分 {len(chunks)} 块 → {out_dir}"
          f"（总字符 {total}，预算 {ns.budget}，重叠 {int(ns.overlap * 100)}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
