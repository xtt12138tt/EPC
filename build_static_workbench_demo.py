from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "portfolio_site" / "static_workbench_demo.html"
SOURCE_URL = "http://127.0.0.1:5050/batch"


MOCK = r'''
<script>
(() => {
  const batchId = "B-DEMO-PORTFOLIO";
  const draft = (projectId, total, stage) => ({
    project_id: projectId,
    fanbao_series: "研究",
    fanbao_type: "用户调研",
    cost_center: "Demo Product",
    budget_owner: "演示负责人",
    screenshot_required: false,
    reimbursement_types: ["礼金(常规)", "兼职", "其他"],
    gift_common: { total_sample: 18, rows: [
      {"测试形式":"实验室测试/座谈会","连续周期":"单日","礼金小类":"基础礼金","样本稀缺性":"千万级","测试时长(小时)":4,"样本量":18,"总金额(元)":4500},
      {"测试形式":"实验室测试/座谈会","连续周期":"单日","礼金小类":"交通补贴","样本量":18,"总金额(元)":900},
      {"测试形式":"实验室测试/座谈会","连续周期":"单日","礼金小类":"转介费","样本量":8,"单价(元)":50,"总金额(元)":400}
    ]},
    parttime: { rows: [{"工作类型":"测试执行","工作难度":"2~4小时/场","测试场次/样本量":8,"总金额(元)":2000}] },
    other: { rows: [{"发包内容":"餐饮费","数量":4,"总金额(元)":120}] },
    payee_rules: {
      expected_total: 24,
      expected_breakdown: {"玩家":18,"渠道接口人":2,"兼职":4},
      default_player_amount: 300,
      known_players: Array.from({length:18}, (_, i) => "Participant " + (i + 1)),
      known_phones: {},
      specific: [
        {"name":"Interface Alpha","type":"渠道接口人","amount":250,"referral_count":5},
        {"name":"Interface Beta","type":"渠道接口人","amount":150,"referral_count":3},
        {"name":"Operator 1","type":"兼职","amount":530},
        {"name":"Operator 2","type":"兼职","amount":530},
        {"name":"Operator 3","type":"兼职","amount":530},
        {"name":"Operator 4","type":"兼职","amount":530}
      ],
      proxies: []
    },
    expense_note: "Demo Product执行了" + projectId + "项目，共产生费用" + total + "元，全部走EPC公账报销。\n详细报销内容为：\n\n【玩家礼金】5400元，共18名玩家参与实验室测试/座谈会，样本单价300元/人（基础礼金250+交通补贴50）。\n\n【转介/接口人费用】400元，2名渠道接口人协助玩家招募转介（合计8人次）。\n\n【兼职费用】2000元，明细为：测试执行共计8场，场次单价250元/场，共计8×250=2000元。\n\n【其他费用】120元，明细为：餐饮费共计4人次，单价30元/人次，共计4×30=120元。",
    grand_total: total,
    warnings: stage === "READY" ? ["演示数据：静态作品集预设状态，不连接真实 EPC。"] : [],
    ai_parse_source: "portfolio static mock"
  });

  const orders = [
    {order_id: batchId + "-01", batch_id: batchId, project_id: "DEMO-2608", status: "READY", current_step: "尚未进入 EPC 填写", warnings: ["演示数据：静态作品集预设状态。"], error: "", source_text: "脱敏签到表 A", meta: {}, draft: draft("DEMO-2608", 7920, "READY")},
    {order_id: batchId + "-02", batch_id: batchId, project_id: "DEMO-2609", status: "RUNNING", current_step: "第 1 页费用明细填写中", warnings: [], error: "", source_text: "脱敏签到表 B", meta: {}, draft: draft("DEMO-2609", 5460, "RUNNING")},
    {order_id: batchId + "-03", batch_id: batchId, project_id: "DEMO-2610", status: "WAITING_PAGE2_APPROVAL", current_step: "收款人差异等待确认", warnings: ["存在姓名模糊候选，需人工确认。"], error: "", source_text: "脱敏签到表 C", meta: {}, draft: draft("DEMO-2610", 3680, "WAITING_PAGE2_APPROVAL")}
  ];
  const events = [
    {kind:"status",message:"READY：演示数据已就绪",created_at:"2026-08-20 11:44:12"},
    {kind:"log",message:"已生成标准 JSON 草稿",created_at:"2026-08-20 11:44:16"},
    {kind:"prompt",message:"第 1 页填写完成后等待确认",created_at:"2026-08-20 11:44:24"}
  ];
  const batch = () => ({batch_id: batchId, title: "脱敏作品集交互 Demo", status: "READY", created_at: "2026-08-20 11:44:00", updated_at: "2026-08-20 11:44:30", orders});
  window.__portfolioDemo = {batchId, orders, batch};
  const findOrder = (url) => orders.find((order) => url.includes(order.order_id)) || orders[0];
  const json = (payload) => new Response(JSON.stringify(payload), {status: 200, headers: {"Content-Type": "application/json"}});
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const method = (init.method || "GET").toUpperCase();
    if (url.includes("/api/batches") && !url.includes("/draft")) {
      if (url.endsWith("/api/batches")) return json({ok:true, batches:[{batch_id:batchId,title:"脱敏作品集交互 Demo",status:"READY",orders_count:3}]});
      return json({ok:true, batch:batch()});
    }
    if (url.includes("/draft") && method === "GET") return json({ok:true, batch_id:batchId, draft_found:true, orders});
    if (url.includes("/api/orders/") && url.includes("/status")) {
      const order = findOrder(url);
      return json({ok:true, order, events, verification:[], running: order.status === "RUNNING", log:[], screenshots:[], waiting: order.status === "WAITING_PAGE2_APPROVAL" ? "收款人差异等待确认" : null});
    }
    if (url.includes("/api/orders/")) {
      const order = findOrder(url);
      if (method === "POST" && url.endsWith("/confirm")) order.status = "READY";
      if (method === "POST" && url.endsWith("/start")) order.status = "RUNNING";
      if (method === "POST" && url.endsWith("/pause")) order.status = "PAUSED";
      if (method === "POST" && url.endsWith("/resume")) order.status = "RUNNING";
      return json({ok:true, order, run_id: order.order_id});
    }
    return json({ok:true});
  };
  window.confirm = () => true;
  window.alert = (message) => console.log("demo alert:", message);
})();
</script>
'''


BOOTSTRAP = r'''
<script>
window.addEventListener("load", () => {
  setTimeout(() => {
    const demo = window.__portfolioDemo;
    if (!demo) return;
    try {
      currentBatchId = demo.batchId;
      orderList = demo.orders;
      const select = document.getElementById("batchSelect");
      if (select) {
        select.innerHTML = `<option value="${demo.batchId}">${demo.batchId}（3单/READY）</option>`;
        select.value = demo.batchId;
      }
      renderOrders();
      renderBatchSummary();
      const hint = document.getElementById("batchHint");
      if (hint) hint.innerHTML = "静态 Demo：已加载 3 张脱敏单据。可点击“查看草稿”“开始”或下方工作台 tab 体验预设状态。";
    } catch (error) {
      console.error("portfolio static bootstrap failed", error);
    }
  }, 50);
});
</script>
'''


def main():
    html = urlopen(SOURCE_URL, timeout=10).read().decode("utf-8")
    if "</head>" not in html:
        raise RuntimeError("Could not find head closing tag in rendered workbench page")
    result = html.replace("</head>", MOCK + "</head>")
    OUTPUT.write_text(result.replace("</body>", BOOTSTRAP + "</body>"), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
