# -*- coding: utf-8 -*-
"""会议纪要 docx 后处理：待办表格版式修复（出稿链第 3 步）。

用法：
    python fix_minutes_docx.py <in.docx> <out.docx>
    python fix_minutes_docx.py <in.docx> <out.docx> --widths 1.15,5.5,2.1,2.2,1.5,3.47

纸型（A4）与 `**加粗**` 已由 article-format v1.5.1 在上游处理，本脚本只修表格：
  1. 固定列宽——纪要表（7 列默认，含状态列；6 列旧件与 8 列变体也认）用实测通过的一组比例；
     其他列数按内容长度比例分配（下限 1.0cm），也可用 --widths 显式指定（须与列数一致）
  2. 单元格段落首行缩进清零——首行缩进挂在 Normal 样式上，仅删直接格式无效，
     须在单元格段落写 w:ind firstLineChars/firstLine=0 显式覆盖，否则窄列
     （如「序号」）首行内容被挤出单元格裁切
  3. 行禁止跨页断行（cantSplit）、表头行跨页重复（tblHeader）
  4. 表头行加浅灰底纹（v1.18.1）——七列全是字，同色时读者要数到第三行才认出表头
"""

import copy
import sys

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm

# 纪要表实测列宽（cm，合计 15.82 = A4 宽 21 - 两道 2.54 边距 - 0.1 余量）
# 7 列 = 序号/待办事项/责任人/完成时间/优先级/状态/备注（v1.13.0 起默认，含状态列）
# 6 列 = v1.12.0 及以前的历史件（无状态列），兼容保留
# 8 列 = 更早的 7 列含「验收标准」再补状态，按比例兜底
#
# v1.18.1 按渲染实测重调：备注列最常见内容是「出处：00:35 张三；强度：确定」，
# 原 2.77cm 下折成三到四行，整行被撑高且读不全。把余量从「待办事项」列
# 匀一部分给备注列，同时保住窄列的硬下限——第一次改到 0.9/1.3cm 时表头
# 「序号」「优先级」被挤成两行、「未开始」也折行（实测二次渲染），故：
#   序号 ≥1.1cm（两个字 + 内边距）、优先级/状态 ≥1.6cm（三字或四个半角字符）
MINUTES_COL_CM = {
    7: [1.1, 3.2, 3.0, 1.95, 1.6, 1.6, 3.35],
    6: [1.1, 5.0, 2.1, 2.1, 1.6, 3.9],
    8: [1.1, 3.8, 1.8, 1.9, 1.5, 1.6, 1.8, 2.5],
}
# 其他列数表格：内容比例分配的列宽下限
MIN_COL_CM = 1.0


def _insert_ordered(parent, elm, successors):
    """按 OOXML schema 顺序插入子元素。"""
    parent.insert_element_before(elm, *successors)


def _available_width_cm(doc):
    """页面可用正文宽度（cm）：页宽 - 左右页边距 - 0.1cm 余量。"""
    sec = doc.sections[0]
    width = sec.page_width.cm - sec.left_margin.cm - sec.right_margin.cm
    return max(width - 0.1, 5.0)


def _proportional_widths(table, total_cm):
    """按各列最长内容长度分配列宽（下限 MIN_COL_CM，归一化到 total_cm）。"""
    ncols = len(table.columns)
    lens = [1.0] * ncols
    for row in table.rows:
        for idx, cell in enumerate(row.cells[:ncols]):
            lens[idx] = max(lens[idx], len(cell.text.strip()) or 1)
    raw = [max(MIN_COL_CM, total_cm * (n / sum(lens))) for n in lens]
    scale = total_cm / sum(raw)
    return [round(w * scale, 2) for w in raw]


def set_cell_margins(tbl, left_right_cm=0.1):
    tblPr = tbl._tbl.tblPr
    mar = tblPr.find(qn("w:tblCellMar"))
    if mar is not None:
        tblPr.remove(mar)
    mar = OxmlElement("w:tblCellMar")
    for tag, cm in (("w:top", 0.0), ("w:left", left_right_cm),
                    ("w:bottom", 0.0), ("w:right", left_right_cm)):
        node = OxmlElement(tag)
        node.set(qn("w:w"), str(int(cm * 567)))
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    # tblCellMar 必须排在 tblLayout 之后、tblLook 之前
    _insert_ordered(tblPr, mar, ("w:tblLook",))


def zero_cell_indent(table):
    """单元格段落显式清零首行缩进（覆盖 Normal 样式的 2 字符缩进）。"""
    cleared = 0
    for row in table.rows:
        for cell in row.cells:
            for par in cell.paragraphs:
                pPr = par._p.get_or_add_pPr()
                ind = pPr.get_or_add_ind()
                ind.set(qn("w:firstLineChars"), "0")
                ind.set(qn("w:firstLine"), "0")
                ind.set(qn("w:leftChars"), "0")
                ind.set(qn("w:left"), "0")
                ind.set(qn("w:rightChars"), "0")
                ind.set(qn("w:right"), "0")
                cleared += 1
    return cleared


def fix_table(table, widths_cm):
    """固定列宽 + 缩进清零 + 禁止跨页断行 + 表头重复。返回处理行数。"""
    table.autofit = False          # 固定布局，防 WPS/Word 自动重算列宽

    tblPr = table._tbl.tblPr
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        _insert_ordered(tblPr, tblW, ("w:jc", "w:tblCellSpacing", "w:tblInd",
                                      "w:tblBorders", "w:shd", "w:tblLayout"))
    tblW.set(qn("w:w"), str(int(sum(widths_cm) * 567)))
    tblW.set(qn("w:type"), "dxa")
    set_cell_margins(table)
    zero_cell_indent(table)

    for idx, cm in enumerate(widths_cm):
        table.columns[idx].width = Cm(cm)

    for row in table.rows:
        trPr = row._tr.get_or_add_trPr()
        if trPr.find(qn("w:cantSplit")) is None:
            _insert_ordered(trPr, OxmlElement("w:cantSplit"),
                            ("w:trHeight", "w:tblHeader", "w:jc", "w:tblCellSpacing",
                             "w:ins", "w:del", "w:trPrChange"))
        for idx, cell in enumerate(row.cells):
            cell.width = Cm(widths_cm[idx])

    # 表头行跨页重复
    trPr = table.rows[0]._tr.get_or_add_trPr()
    if trPr.find(qn("w:tblHeader")) is None:
        _insert_ordered(trPr, OxmlElement("w:tblHeader"),
                        ("w:jc", "w:tblCellSpacing", "w:ins", "w:del", "w:trPrChange"))
    shade_header_row(table)
    return len(table.rows)


def shade_header_row(table, fill="F2F2F2"):
    """给表头行加浅灰底纹。

    为什么在纪要侧补而不是改 profile：enterprise-doc 的 `table.shading` 是 false
    （公文表格传统上不加底纹），但纪要待办表 7 列全是字，表头与正文同色时
    读者要数到第三行才认出哪行是表头（实测渲染）。这是纪要特有的可读性需求，
    不值得为它去动全局 profile——那会让所有企业公文表格都变底纹。
    """
    if not table.rows:
        return 0
    shaded = 0
    for cell in table.rows[0].cells:
        tcPr = cell._tc.get_or_add_tcPr()
        shd = tcPr.find(qn("w:shd"))
        if shd is not None:
            tcPr.remove(shd)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        # w:shd 在 tcPr 里的位置必须在 tcW 之后、tcMar/vAlign 之前
        _insert_ordered(tcPr, shd, ("w:noWrap", "w:tcMar", "w:textDirection",
                                    "w:tcFitText", "w:vAlign", "w:hideMark"))
        shaded += 1
    return shaded


def parse_widths(arg):
    try:
        vals = [float(x) for x in arg.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"--widths 解析失败：{arg!r}（应为逗号分隔的数字）")
    if not vals or any(v <= 0 for v in vals):
        raise SystemExit(f"--widths 取值非法：{arg!r}")
    return vals


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    explicit = None
    for i, a in enumerate(argv):
        if a == "--widths" and i + 1 < len(argv):
            explicit = parse_widths(argv[i + 1])
    if len(args) < 2:
        sys.stderr.write(__doc__ + "\n")
        return 2

    src, dst = args[0], args[1]
    doc = Document(src)
    total = _available_width_cm(doc)
    reports = []
    for idx, table in enumerate(doc.tables):
        ncols = len(table.columns)
        if explicit is not None:
            if len(explicit) != ncols:
                raise SystemExit(
                    f"--widths 给了 {len(explicit)} 列，表 {idx} 为 {ncols} 列，不一致")
            widths = explicit
        elif ncols in MINUTES_COL_CM:
            # 纪要表用实测列宽；页面不是 A4+2.54cm 时按可用宽度等比缩放，防溢出
            base = MINUTES_COL_CM[ncols]
            if abs(sum(base) - total) > 0.05:
                k = total / sum(base)
                widths = [round(w * k, 2) for w in base]
            else:
                widths = base
        else:
            widths = _proportional_widths(table, total)
        rows = fix_table(table, widths)
        reports.append(f"表{idx}（{ncols} 列 {rows} 行）列宽 "
                       + "/".join(f"{w:.2f}" for w in widths))
    doc.save(dst)
    print(f"已修复 {src} -> {dst}")
    for line in reports:
        print("  " + line)
    print("  单元格首行缩进清零、行禁止跨页断行、表头跨页重复与底纹")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
