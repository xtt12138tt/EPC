from batch_store import Store
from expense_note import build_standard_expense_note


def main():
    store = Store()
    if store.list_batches():
        return

    batch = store.create_batch(
        title="脱敏作品集演示",
        orders=[{
            "project_id": "DEMO-2608",
            "source_text": "DEMO 项目：18 名玩家、2 名渠道接口人、4 名测试执行兼职。",
            "meta": {"source": "portfolio-demo"},
        }],
    )
    order = batch["orders"][0]
    draft = {
        "project_id": "DEMO-2608",
        "fanbao_series": "研究",
        "fanbao_type": "用户调研",
        "cost_center": "Demo Product",
        "screenshot_required": False,
        "reimbursement_types": ["礼金(常规)", "兼职", "其他"],
        "gift_common": {
            "total_sample": 18,
            "rows": [
                {"测试形式": "实验室测试/座谈会", "连续周期": "单日", "礼金小类": "基础礼金", "样本稀缺性": "千万级", "测试时长(小时)": 4, "样本量": 18, "总金额(元)": 4500},
                {"测试形式": "实验室测试/座谈会", "连续周期": "单日", "礼金小类": "交通补贴", "样本量": 18, "总金额(元)": 900},
                {"测试形式": "实验室测试/座谈会", "连续周期": "单日", "礼金小类": "转介费", "样本量": 8, "单价(元)": 50, "总金额(元)": 400},
            ],
        },
        "parttime": {
            "rows": [
                {"工作类型": "测试执行", "工作难度": "2~4小时/场", "测试场次/样本量": 8, "总金额(元)": 2000},
            ],
        },
        "other": {"rows": [{"发包内容": "餐饮费", "数量": 4, "总金额(元)": 120}]},
        "payee_rules": {
            "expected_total": 24,
            "expected_breakdown": {"玩家": 18, "渠道接口人": 2, "兼职": 4},
            "default_player_amount": 300,
            "known_players": [f"Participant {index}" for index in range(1, 19)],
            "known_phones": {},
            "specific": [
                {"name": "Interface Alpha", "type": "渠道接口人", "amount": 250, "referral_count": 5},
                {"name": "Interface Beta", "type": "渠道接口人", "amount": 150, "referral_count": 3},
                {"name": "Operator 1", "type": "兼职", "amount": 530},
                {"name": "Operator 2", "type": "兼职", "amount": 530},
                {"name": "Operator 3", "type": "兼职", "amount": 530},
                {"name": "Operator 4", "type": "兼职", "amount": 530},
            ],
            "proxies": [],
        },
        "grand_total": 7920,
        "warnings": ["演示数据：不连接真实 EPC，不包含真实个人信息。"],
    }
    draft["expense_note"] = build_standard_expense_note(draft)
    store.update_order(order["order_id"], draft=draft, status="READY", warnings=draft["warnings"])


if __name__ == "__main__":
    main()
