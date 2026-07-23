from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from html import escape
from pathlib import Path

_REQUIRED_COLUMNS = frozenset({"date", "product", "amount"})


@dataclass(frozen=True)
class SalesRow:
    date: str
    product: str
    amount: Decimal


def load_sales_rows(input_path: Path) -> list[SalesRow]:
    if input_path.suffix.lower() != ".csv":
        raise ValueError("输入文件必须是 .csv")
    if not input_path.exists() or not input_path.is_file():
        raise ValueError(f"输入文件不存在或不是普通文件: {input_path}")

    try:
        with input_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            reader.fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
            missing = sorted(_REQUIRED_COLUMNS - set(reader.fieldnames))
            if missing:
                raise ValueError(f"CSV 缺少必填列: {', '.join(missing)}")

            rows: list[SalesRow] = []
            for row_number, raw in enumerate(reader, start=2):
                if not any(str(value or "").strip() for value in raw.values()):
                    continue
                rows.append(
                    SalesRow(
                        date=_required_text(raw.get("date"), field_name="date", row_number=row_number),
                        product=_required_text(raw.get("product"), field_name="product", row_number=row_number),
                        amount=_parse_amount(raw.get("amount"), row_number=row_number),
                    )
                )
    except UnicodeDecodeError as exc:
        raise ValueError("输入文件必须使用 UTF-8 或 UTF-8 BOM 编码") from exc

    if not rows:
        raise ValueError("CSV 没有有效数据行")
    return rows


def build_report(rows: list[SalesRow]) -> str:
    if not rows:
        raise ValueError("销售数据不能为空")

    with localcontext() as context:
        context.prec = _calculation_precision(rows)
        daily_totals: dict[str, Decimal] = defaultdict(Decimal)
        product_totals: dict[str, Decimal] = defaultdict(Decimal)
        for row in rows:
            daily_totals[row.date] += row.amount
            product_totals[row.product] += row.amount

        total = sum((row.amount for row in rows), Decimal())
        average = total / Decimal(len(rows))

    top_product, top_amount = sorted(product_totals.items(), key=lambda item: (-item[1], item[0]))[0]
    top_product_text = _markdown_text(top_product)

    lines = [
        "# 销售 CSV 报表",
        "",
        "## 数据概览",
        "",
        f"- 有效订单数：{len(rows)}",
        f"- 总销售额：{_format_money(total)}",
        f"- 平均订单金额：{_format_money(average)}",
        f"- 销售额最高产品：{top_product_text}（{_format_money(top_amount)}）",
        "",
        "## 每日销售额",
        "",
        "| 日期 | 销售额 |",
        "| --- | ---: |",
    ]
    lines.extend(
        f"| {_markdown_text(date)} | {_format_money(amount)} |" for date, amount in sorted(daily_totals.items())
    )
    lines.extend(
        [
            "",
            "## 产品销售额",
            "",
            "| 产品 | 销售额 |",
            "| --- | ---: |",
        ]
    )
    lines.extend(
        f"| {_markdown_text(product)} | {_format_money(amount)} |"
        for product, amount in sorted(product_totals.items(), key=lambda item: (-item[1], item[0]))
    )
    return "\n".join(lines) + "\n"


def write_report(input_path: Path, output_path: Path) -> Path:
    if output_path.suffix.lower() != ".md":
        raise ValueError("输出文件必须是 .md")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("输出文件不能覆盖输入文件")

    report = build_report(load_sales_rows(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    return output_path


def _required_text(value: str | None, *, field_name: str, row_number: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"第 {row_number} 行 {field_name} 不能为空")
    return normalized


def _calculation_precision(rows: list[SalesRow]) -> int:
    integer_digits = max(max(row.amount.adjusted() + 1, 1) for row in rows)
    fractional_digits = max(max(-row.amount.as_tuple().exponent, 0) for row in rows)
    carry_digits = len(str(len(rows)))
    return max(28, integer_digits + fractional_digits + carry_digits + 2)


def _markdown_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return escape(normalized, quote=False).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _parse_amount(value: str | None, *, row_number: int) -> Decimal:
    normalized = str(value or "").strip()
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"第 {row_number} 行 amount 不是合法有限数值") from exc
    if not amount.is_finite():
        raise ValueError(f"第 {row_number} 行 amount 不是合法有限数值")
    return amount


def _format_money(value: Decimal) -> str:
    with localcontext() as context:
        context.rounding = ROUND_HALF_UP
        return format(value, ".2f")


def main() -> int:
    parser = argparse.ArgumentParser(description="根据销售 CSV 生成 Markdown 报表")
    parser.add_argument("--input", required=True, type=Path, help="输入 CSV 文件")
    parser.add_argument("--output", required=True, type=Path, help="输出 Markdown 文件")
    args = parser.parse_args()

    try:
        output_path = write_report(args.input, args.output)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(output_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
