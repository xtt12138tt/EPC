"""Unified EPC expense-note formatter."""

from __future__ import annotations

import re
from typing import Any


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
        return float(match.group(0)) if match else None


def _fmt(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")


def _rows(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [row for row in (value.get("rows") or []) if isinstance(row, dict)]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _sum_rows(rows: list[dict]) -> float:
    return sum(_number(row.get("总金额(元)")) or 0 for row in rows)


def _unit(total: float, quantity: float | None) -> str:
    if quantity and quantity > 0:
        return _fmt(total / quantity)
    return ""


def _quantity_label(content: str, quantity: float) -> str:
    if any(token in content for token in ("餐", "饮", "交通", "交补")):
        return f"{_fmt(quantity)}人次"
    return _fmt(quantity)


def build_standard_expense_note(data: dict) -> str:
    """Build the single canonical EPC expense-note format from normalized order data."""
    project_id = str(data.get("project_id") or "").strip()
    cost_center = str(data.get("cost_center") or "").strip()
    grand_total = _number(data.get("grand_total"))
    if grand_total is None:
        grand_total = (
            _sum_rows(_rows(data.get("gift_common")))
            + _sum_rows(_rows(data.get("parttime")))
            + _sum_rows(_rows(data.get("other")))
        )

    prefix = f"{cost_center}执行了{project_id}项目，共产生费用{_fmt(grand_total)}元，全部走EPC公账报销。"
    sections = []

    gift_rows = _rows(data.get("gift_common"))
    base_rows = [
        row for row in gift_rows
        if str(row.get("礼金小类") or "").strip() in {"基础礼金", "超时礼金", "应急礼金"}
    ]
    transport_rows = [
        row for row in gift_rows
        if str(row.get("礼金小类") or "").strip() == "交通补贴"
    ]
    referral_rows = [
        row for row in gift_rows
        if str(row.get("礼金小类") or "").strip() == "转介费"
    ]
    base_total = _sum_rows(base_rows)
    transport_total = _sum_rows(transport_rows)
    player_total = base_total + transport_total
    player_count = _number((data.get("gift_common") or {}).get("total_sample"))
    if player_count is None:
        player_count = _number((data.get("payee_rules") or {}).get("expected_breakdown", {}).get("玩家"))
    if player_total > 0:
        player_unit = _unit(player_total, player_count)
        test_form = str(data.get("test_form") or "实验室测试/座谈会").strip()
        text = f"【玩家礼金】{_fmt(player_total)}元"
        if player_count:
            text += f"，共{_fmt(player_count)}名玩家参与{test_form}"
        if player_unit:
            text += f"，样本单价{player_unit}元/人"
        if base_total > 0 and transport_total > 0 and player_count:
            text += f"（基础礼金{_unit(base_total, player_count)}+交通补贴{_unit(transport_total, player_count)}）"
        text += "。"
        sections.append(text)

    payee_rules = data.get("payee_rules") or {}
    interfaces = [
        item for item in (payee_rules.get("specific") or [])
        if str(item.get("type") or "").strip() in {"渠道接口人", "接口人", "KOL"}
    ]
    referral_total = _sum_rows(referral_rows)
    if referral_total <= 0:
        referral_total = sum(_number(item.get("amount")) or 0 for item in interfaces)
    referral_count = sum(_number(item.get("referral_count")) or 0 for item in interfaces)
    if referral_count <= 0:
        referral_count = sum(_number(row.get("样本量")) or 0 for row in referral_rows)
    if referral_total > 0:
        text = f"【转介/接口人费用】{_fmt(referral_total)}元"
        if interfaces:
            text += f"，{len(interfaces)}名渠道接口人协助玩家招募转介"
        if referral_count:
            text += f"（合计{_fmt(referral_count)}人次）"
        if interfaces:
            detail = "、".join(
                f"{item.get('name', '')}{_fmt(item.get('amount'))}元" for item in interfaces
            )
            text += f"，费用分别为：{detail}"
        text += "。"
        sections.append(text)

    parttime_rows = _rows(data.get("parttime"))
    parttime_total = _sum_rows(parttime_rows)
    if parttime_total > 0:
        details = []
        for row in parttime_rows:
            work_type = str(row.get("工作类型") or "兼职").strip()
            quantity = _number(row.get("测试场次/样本量"))
            total = _number(row.get("总金额(元)")) or 0
            if quantity and quantity > 0:
                is_session_work = work_type in {"测试执行", "实验室执行"}
                quantity_unit = "场" if is_session_work else "个样本"
                price_label = "场次单价" if is_session_work else "样本单价"
                price_unit = "场" if is_session_work else "个"
                details.append(
                    f"{work_type}共计{_fmt(quantity)}{quantity_unit}，"
                    f"{price_label}{_unit(total, quantity)}元/{price_unit}，"
                    f"共计{_fmt(quantity)}×{_unit(total, quantity)}={_fmt(total)}元"
                )
            else:
                details.append(f"{work_type}{_fmt(total)}元")
        sections.append(f"【兼职费用】{_fmt(parttime_total)}元，明细为：{'、'.join(details)}。")

    other_rows = _rows(data.get("other"))
    other_total = _sum_rows(other_rows)
    if other_total > 0:
        details = []
        for row in other_rows:
            content = str(row.get("发包内容") or "其他").strip()
            quantity = _number(row.get("数量"))
            total = _number(row.get("总金额(元)")) or 0
            if quantity and quantity > 0:
                unit = _unit(total, quantity)
                if any(token in content for token in ("餐", "饮", "交通", "交补")):
                    details.append(
                        f"{content}共计{_fmt(quantity)}人次，单价{unit}元/人次，"
                        f"共计{_fmt(quantity)}×{unit}={_fmt(total)}元"
                    )
                else:
                    details.append(f"{content}{_quantity_label(content, quantity)}×{unit}={_fmt(total)}元")
            else:
                details.append(f"{content}{_fmt(total)}元")
        sections.append(f"【其他费用】{_fmt(other_total)}元，明细为：{'、'.join(details)}。")

    return prefix + "\n详细报销内容为：\n\n" + "\n\n".join(sections)
