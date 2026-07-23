from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from yuxi.agents.skills.buildin import BUILTIN_SKILLS


def _load_report_module():
    spec_item = next((item for item in BUILTIN_SKILLS if item.slug == "sales-csv-report"), None)
    assert spec_item is not None, "sales-csv-report builtin skill spec not found"
    script_path = spec_item.source_dir / "scripts" / "build_report.py"
    module_spec = importlib.util.spec_from_file_location("sales_csv_report_build_report", script_path)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    path.write_text(content, encoding=encoding)
    return path


def test_build_report_summarizes_sales(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(
        tmp_path / "sales.csv",
        "date,product,amount\n2026-07-01,Alpha,100.00\n2026-07-01,Beta,50\n2026-07-02,Alpha,25.50\n",
    )

    report = module.build_report(module.load_sales_rows(input_path))

    assert "- 有效订单数：3" in report
    assert "- 总销售额：175.50" in report
    assert "- 平均订单金额：58.50" in report
    assert "- 销售额最高产品：Alpha（125.50）" in report
    assert "| 2026-07-01 | 150.00 |" in report
    assert "| Alpha | 125.50 |" in report


def test_build_report_escapes_markdown_in_product_names(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(
        tmp_path / "sales.csv",
        'date,product,amount\n2026-07-01,"Alpha|Premium\nLine",10\n',
    )

    report = module.build_report(module.load_sales_rows(input_path))

    assert "- 销售额最高产品：Alpha\\|Premium<br>Line（10.00）" in report
    assert "| Alpha\\|Premium<br>Line | 10.00 |" in report


def test_build_report_formats_large_finite_amount(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(
        tmp_path / "sales.csv",
        "date,product,amount\n2026-07-01,Alpha,1000000000000000000000000000\n2026-07-01,Alpha,0.01\n",
    )

    report = module.build_report(module.load_sales_rows(input_path))

    assert "- 总销售额：1000000000000000000000000000.01" in report
    assert "- 平均订单金额：500000000000000000000000000.01" in report
    assert "- 销售额最高产品：Alpha（1000000000000000000000000000.01）" in report
    assert "| 2026-07-01 | 1000000000000000000000000000.01 |" in report


def test_load_sales_rows_accepts_utf8_bom(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(
        tmp_path / "sales.csv",
        "date,product,amount\n2026-07-01,Alpha,10\n",
        encoding="utf-8-sig",
    )

    rows = module.load_sales_rows(input_path)

    assert rows[0].date == "2026-07-01"
    assert rows[0].product == "Alpha"
    assert str(rows[0].amount) == "10"


def test_load_sales_rows_trims_column_names(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(
        tmp_path / "sales.csv",
        " date , product , amount \n2026-07-01,Alpha,10\n",
    )

    rows = module.load_sales_rows(input_path)

    assert rows[0].date == "2026-07-01"
    assert rows[0].product == "Alpha"
    assert str(rows[0].amount) == "10"


def test_load_sales_rows_rejects_missing_columns(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(tmp_path / "sales.csv", "date,product\n2026-07-01,Alpha\n")

    with pytest.raises(ValueError, match="CSV 缺少必填列: amount"):
        module.load_sales_rows(input_path)


def test_load_sales_rows_rejects_invalid_amount(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(tmp_path / "sales.csv", "date,product,amount\n2026-07-01,Alpha,oops\n")

    with pytest.raises(ValueError, match="第 2 行 amount 不是合法有限数值"):
        module.load_sales_rows(input_path)


def test_load_sales_rows_rejects_empty_data(tmp_path: Path) -> None:
    module = _load_report_module()
    input_path = _write_csv(tmp_path / "sales.csv", "date,product,amount\n")

    with pytest.raises(ValueError, match="CSV 没有有效数据行"):
        module.load_sales_rows(input_path)


def test_main_writes_markdown_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_report_module()
    input_path = _write_csv(tmp_path / "sales.csv", "date,product,amount\n2026-07-01,Alpha,10\n")
    output_path = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_report.py", "--input", str(input_path), "--output", str(output_path)],
    )

    assert module.main() == 0
    assert output_path.exists()
    assert "- 总销售额：10.00" in output_path.read_text(encoding="utf-8")
