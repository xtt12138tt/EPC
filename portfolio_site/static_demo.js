(function () {
  const root = document.getElementById('demo');
  if (!root) return;

  const orders = [
    { id: 'DEMO-2608', code: 'B20260820-001-01', status: '可执行', color: '#0f8b6d', amount: '7,920 元', progress: '尚未进入 EPC 填写', risk: '1 项待关注' },
    { id: 'DEMO-2609', code: 'B20260820-001-02', status: '填写中', color: '#1779d4', amount: '5,460 元', progress: '第 1 页费用明细填写中', risk: '运行中' },
    { id: 'DEMO-2610', code: 'B20260820-001-03', status: '待确认', color: '#a46a00', amount: '3,680 元', progress: '收款人差异等待确认', risk: '1 项需处理' }
  ];

  const runStates = [
    { code: 'QUEUED', label: '排队中', detail: '等待可用浏览器标签页', tone: '' },
    { code: 'RUNNING', label: '填写第 1 页', detail: '项目、费用说明和明细填写中', tone: '' },
    { code: 'WAITING_PAGE1_APPROVAL', label: '等待第 1 页确认', detail: '已截图，等待人工检查', tone: 'wait' },
    { code: 'FILLING_PAYEES', label: '填写收款人', detail: '正在匹配姓名、手机号和金额', tone: '' },
    { code: 'VERIFYING_PAYEES', label: '收款人核验', detail: '正在回读类型、金额和支付宝字段', tone: '' },
    { code: 'WAITING_PAGE2_APPROVAL', label: '等待核验确认', detail: '差异已处理，等待确认完成', tone: 'wait' },
    { code: 'COMPLETED', label: '提报完成', detail: '第 2 页核验完成，未自动最终提交', tone: 'done' }
  ];

  let selectedOrder = 0;
  let activeTab = '批次任务看板';
  let runIndex = 1;
  let timer = null;

  const join = (parts) => parts.join('');
  const action = (label, key, value) => '<button class="ghost-action" data-' + key + '="' + value + '">' + label + '</button>';
  const field = (value) => '<div class="actual-table-cell"><div class="fake-input">' + value + '</div></div>';
  const row = (values, payee) => '<div class="actual-table-row' + (payee ? ' payee' : '') + '">' + values.map(field).join('') + '</div>';
  const header = (values, payee) => '<div class="actual-table-row header' + (payee ? ' payee' : '') + '">' + values.map((value) => '<div class="actual-table-cell">' + value + '</div>').join('') + '</div>';

  function workspaceHeader(order, tab) {
    const tabs = ['单据概览', '费用明细', '收款人', '校验结果', '标准 JSON'];
    return join([
      '<div class="workspace-header">',
        '<div><b>当前单据工作台 ｜ ', order.id, '</b>',
        '<div class="workspace-sub">单据编号：', order.code, '　当前状态：<span style="color:', order.color, ';font-weight:700">', order.status, '</span></div></div>',
        '<div class="workspace-actions">',
          action('保存草稿为 JSON 文件', 'tab', '标准 JSON'),
          action('刷新本单', 'tab', tab),
          action('开始填报', 'run', '1'),
        '</div>',
      '</div>',
      '<div class="demo-tabs">',
        tabs.map((item) => '<button class="demo-tab ' + (item === tab ? 'active' : '') + '" data-tab="' + item + '">' + item + '</button>').join(''),
      '</div>'
    ]);
  }

  function batchBoard() {
    return join([
      '<div class="workspace-header"><div><b>批量提报 · 批次工作台</b><div class="workspace-sub">批次：B20260820-001　共 3 张演示单</div></div>',
      '<div class="workspace-actions">', action('刷新批次', 'view', 'batch'), action('加载 Codex 批次草稿', 'view', 'batch'), '</div></div>',
      '<div class="mock-panel" style="margin-top:14px"><div class="mock-head"><span>原始资料</span><span style="color:#1779d4">保存给 Codex 解析</span></div><div class="input-line"></div><div class="input-line"></div></div>',
      '<div class="workspace-metrics" style="margin-top:14px">',
        '<div class="workspace-metric"><span>总单数</span><b>3</b></div>',
        '<div class="workspace-metric"><span>可执行</span><b style="color:#0f8b6d">1</b></div>',
        '<div class="workspace-metric"><span>执行中</span><b style="color:#1779d4">1</b></div>',
        '<div class="workspace-metric"><span>等待确认</span><b style="color:#a46a00">1</b></div>',
      '</div>',
      '<div class="actual-form-section" style="margin-top:14px"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">单据 / 项目</span><span>静态预设状态</span></div>',
      '<div class="actual-table">',
        header(['单据 / 项目', '状态', '当前进度', '应报金额', '操作'], false),
        row([orders[0].id, orders[0].status, orders[0].progress, orders[0].amount, '查看草稿'], false),
        row([orders[1].id, orders[1].status, orders[1].progress, orders[1].amount, '查看进度'], false),
        row([orders[2].id, orders[2].status, orders[2].progress, orders[2].amount, '处理差异'], false),
      '</div>',
      '<div class="workspace-actions" style="margin-top:10px">',
        '<button class="ghost-action" data-order="0">查看草稿</button>',
        '<button class="ghost-action" data-order="1">查看运行中状态</button>',
        '<button class="ghost-action" data-order="2">查看待确认状态</button>',
      '</div></div>'
    ]);
  }

  function overview(order) {
    return join([
      workspaceHeader(order, '单据概览'),
      '<div class="workspace-metrics">',
        '<div class="workspace-metric"><span>项目编号</span><b>', order.id, '</b></div>',
        '<div class="workspace-metric"><span>成本归属</span><b>Demo Product</b></div>',
        '<div class="workspace-metric"><span>收款人</span><b>24 人</b></div>',
        '<div class="workspace-metric"><span>应报总额</span><b>', order.amount, '</b></div>',
      '</div>',
      '<div class="actual-form-section"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">收款人规则</span><span>玩家 18 ｜ 接口人 2 ｜ 兼职 4</span></div>',
      '<div class="rule-output-row"><span>玩家默认金额</span><b>300 元 / 人</b></div>',
      '<div class="rule-output-row"><span>渠道接口人转介费</span><b>8 人次 / 400 元</b></div>',
      '<div class="rule-output-row"><span>兼职实际收款</span><b>530 元 / 人</b></div></div>',
      '<div class="actual-form-section"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">费用说明</span><span>统一模板</span></div>',
      '<p style="margin:0;font-size:13px;line-height:1.8">Demo Product 执行了 ', order.id, ' 项目，共产生费用 ', order.amount, '。玩家礼金、转介/接口人费用、测试执行费用和餐饮费用已分别生成。</p></div>',
      '<div class="check-list" style="margin-top:14px"><div class="check"><b>✓</b>费用明细合计 = 总报销金额</div><div class="check"><b>✓</b>收款人合计 = 总报销金额</div><div class="check">⚠ 兼职工作难度待确认</div></div>'
    ]);
  }

  function expense(order) {
    return join([
      workspaceHeader(order, '费用明细'),
      '<div class="actual-form-section"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">国内玩家礼金（常规）</span><span>总样本量 18</span></div><div class="actual-table">',
      header(['礼金小类', '测试形式', '周期', '稀缺性', '时长', '样本量', '总金额'], false),
      row(['基础礼金', '实验室测试/座谈会', '单日', '千万级', '4', '18', '4500'], false),
      row(['交通补贴', '实验室测试/座谈会', '单日', '-', '-', '18', '900'], false),
      row(['转介费', '实验室测试/座谈会', '单日', '-', '-', '8', '400'], false),
      '</div></div>',
      '<div class="actual-form-section"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">国内兼职</span><span>测试执行</span></div><div class="actual-table">',
      header(['工作类型', '工作难度', '场次/样本量', '标准单价', '总金额', '', ''], false),
      row(['测试执行', '2~4小时/场', '8', '250', '2000', '', ''], false),
      '</div></div>',
      '<div class="actual-form-section"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">其他费用</span><span>餐饮费</span></div><div class="actual-table">',
      header(['发包内容', '数量', '单价', '总金额', '', '', ''], false),
      row(['餐饮费', '4', '30', '120', '', '', ''], false),
      '</div></div>'
    ]);
  }

  function payees(order) {
    return join([
      workspaceHeader(order, '收款人'),
      '<div class="actual-form-section"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">玩家</span><span>默认金额 300 元</span></div><div class="actual-table">',
      header(['姓名', '手机号', '默认金额', '操作'], true),
      row(['Participant 1', '138****1001', '300', '删除'], true),
      row(['Participant 2', '138****1002', '300', '删除'], true),
      row(['Participant 3', '138****1003', '300', '删除'], true),
      row(['Participant 4', '138****1004', '300', '删除'], true),
      '</div></div>',
      '<div class="actual-form-section"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">渠道接口人 / 转介</span><span>合计 8 人次</span></div><div class="actual-table">',
      header(['姓名', '转介人数', '收款金额', '操作'], true),
      row(['Interface Alpha', '5', '250', '删除'], true),
      row(['Interface Beta', '3', '150', '删除'], true),
      '</div></div>',
      '<div class="actual-form-section"><div class="actual-form-head"><span style="color:#102a43;font-size:16px">兼职收款人</span><span>4 人</span></div><div class="actual-table">',
      header(['姓名', '收款金额', '确认方式', '操作'], true),
      row(['Operator 1', '530', '场次费 + 餐补', '删除'], true),
      row(['Operator 2', '530', '场次费 + 餐补', '删除'], true),
      '</div></div>'
    ]);
  }

  function validation(order) {
    const mustFix = order.id === 'DEMO-2610'
      ? [['同名 / 不同手机号', 'Participant 2 存在两个手机号候选，无法确认实际收款人。', '阻断执行']]
      : [];
    const possibleIssues = order.id === 'DEMO-2608'
      ? [['兼职工作难度待确认', '会影响兼职费用类目的最终判断，建议在执行前补充。', '建议确认']]
      : order.id === 'DEMO-2609'
        ? [['第 1 页截图待人工确认', '费用明细已填写，需确认页面内容与审核草稿一致。', '等待确认']]
        : [['同手机号 / 不同姓名', '检测到可能为同一人，但姓名字段存在差异，建议核实后再继续。', '建议确认']];
    const renderGroup = function (type, title, items, emptyText) {
      const rows = items.length
        ? items.map((item) => '<div class="validation-item"><b>' + item[0] + '</b><span>' + item[1] + '</span><span class="validation-tag ' + type + '">' + item[2] + '</span></div>').join('')
        : '<div class="validation-empty">✓ ' + emptyText + '</div>';
      return '<div class="validation-group ' + type + '"><div class="validation-group-head"><span>' + title + '</span><span class="validation-count">' + items.length + ' 项</span></div>' + rows + '</div>';
    };
    return join([
      workspaceHeader(order, '校验结果'),
      '<p class="demo-label" style="margin:0 0 10px">校验结果按处理优先级展示：必须修改项会阻断执行；可能存在错误项只提示人工核实。</p>',
      renderGroup('required', '必须修改项', mustFix, '当前单据没有阻断执行的问题。'),
      renderGroup('possible', '可能存在错误项', possibleIssues, '当前未发现需要额外核实的信息。'),
      '<div class="check-list" style="margin-top:14px"><div class="check"><b>✓</b>费用明细合计 = 总报销金额</div><div class="check"><b>✓</b>收款人金额合计 = 费用明细总额</div><div class="check">⚠ 支付宝登录号、实名或 EPC 红色校验异常会回到前端处理</div></div>'
    ]);
  }

  function jsonView(order) {
    return workspaceHeader(order, '标准 JSON') + '<div class="json-box">{\n  "project_id": "' + order.id + '",\n  "gift_common": [\n    {"type":"基础礼金","amount":4500},\n    {"type":"交通补贴","amount":900},\n    {"type":"转介费","amount":400}\n  ],\n  "parttime": [{"work":"测试执行","count":8,"amount":2000}],\n  "other": [{"content":"餐饮费","count":4,"amount":120}],\n  "payee_rules": {"expected_total":24},\n  "grand_total": 7920,\n  "warnings": ["兼职工作难度待确认"]\n}</div>';
  }

  function running(order) {
    const state = runStates[runIndex];
    const tone = state.tone ? ' ' + state.tone : '';
    return join([
      workspaceHeader(order, '单据概览'),
      '<div class="run-panel"><div class="run-head"><div><span class="run-badge', tone, '">', state.code, '</span><span style="margin-left:8px;font-size:13px">', state.detail, '</span></div><div class="run-actions">',
      '<button class="run-action" data-run="prev">上一步</button><button class="run-action" data-run="next">下一状态</button></div></div>',
      '<div class="run-track">',
      runStates.map((item, index) => '<div class="run-step' + (index < runIndex ? ' done' : index === runIndex ? (item.tone === 'wait' ? ' wait' : ' active') : '') + '"><strong>' + item.code + '</strong><span>' + item.label + '</span></div>').join(''),
      '</div><pre class="run-log">11:44:12  打开 EPC 报销页\n11:44:15  已选择项目 ' + order.id + '\n11:44:18  已填写费用说明\n11:44:21  当前状态：' + state.label + '\n下一步：' + state.detail + '</pre></div>'
    ]);
  }

  function render() {
    const order = orders[selectedOrder];
    let title = '批次任务看板';
    let hint = '先查看三张单的可执行状态、填写进度和待确认事项。';
    let body = batchBoard();
    if (activeTab === '运行中状态') { title = '运行中状态'; hint = '模拟填写过程中状态机的变化；点击“下一状态”查看后续确认和核验节点。'; body = running(orders[1]); }
    if (activeTab === '单据概览') { title = '当前单据概览'; hint = '模拟单据概览：项目字段、收款人规则和费用说明保持同一张单的状态。'; body = overview(order); }
    if (activeTab === '费用明细') { title = '费用明细'; hint = '按 EPC 实际子表展示常规礼金、兼职和其他费用。'; body = expense(order); }
    if (activeTab === '收款人') { title = '收款人规则'; hint = '按玩家、渠道接口人和兼职分别维护应收信息，并在校验结果中做双重确认。'; body = payees(order); }
    if (activeTab === '校验结果') { title = '校验结果'; hint = '手机号优先、姓名兜底、模糊候选需人工确认；不会因为切换 tab 改变当前单据状态。'; body = validation(order); }
    if (activeTab === '标准 JSON') { title = '标准 JSON'; hint = '前端审核与浏览器执行共用这一份数据，避免重复计算和口径漂移。'; body = jsonView(order); }

    root.innerHTML = join([
      '<div class="wrap"><h2>可点击的工作台 Demo</h2>',
      '<div class="demo"><aside class="demo-side"><h3>当前提报流程</h3><p>预置三张单：可执行、填写中、收款人待确认。点击步骤、tab 或按钮查看不同状态。</p>',
      '<ul class="step-list">',
      [['批次任务看板','批次任务看板'],['运行中状态','运行中状态'],['单据概览','单据概览'],['费用明细','费用明细'],['收款人','收款人'],['标准 JSON','标准 JSON']].map((item, index) => '<li class="' + (activeTab === item[1] ? 'active' : '') + '" data-view="' + item[1] + '"><span class="step-dot">' + (index + 1) + '</span>' + item[0] + '</li>').join(''),
      '</ul><button class="button" data-demo-action="play">自动播放</button></aside>',
      '<div class="demo-main"><div class="demo-top"><div><b>' + title + '</b><div class="demo-label">' + hint + '</div></div><span class="chip">静态 Demo</span></div><div class="demo-stage">' + body + '</div></div></div></div>'
    ]);

    root.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => {
      activeTab = button.dataset.view;
      if (activeTab === '运行中状态') selectedOrder = 1;
      if (activeTab === '费用明细' || activeTab === '收款人' || activeTab === '标准 JSON' || activeTab === '校验结果') selectedOrder = 0;
      render();
    }));
    root.querySelectorAll('[data-order]').forEach((button) => button.addEventListener('click', () => {
      selectedOrder = Number(button.dataset.order);
      activeTab = selectedOrder === 1 ? '运行中状态' : selectedOrder === 2 ? '收款人' : '单据概览';
      render();
    }));
    if (activeTab === '批次任务看板') {
      root.querySelectorAll('.actual-table-row:not(.header)').forEach((rowNode, index) => {
        rowNode.style.cursor = 'pointer';
        rowNode.addEventListener('click', () => {
          selectedOrder = index;
          activeTab = index === 1 ? '运行中状态' : index === 2 ? '收款人' : '单据概览';
          render();
        });
      });
    }
    root.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => {
      activeTab = button.dataset.tab;
      render();
    }));
    root.querySelectorAll('[data-run]').forEach((button) => button.addEventListener('click', () => {
      if (button.dataset.run === 'next') runIndex = Math.min(runIndex + 1, runStates.length - 1);
      if (button.dataset.run === 'prev') runIndex = Math.max(runIndex - 1, 0);
      render();
    }));
    root.querySelectorAll('[data-demo-action=\"play\"]').forEach((button) => button.addEventListener('click', () => {
      let index = 0;
      const flow = ['批次任务看板', '运行中状态', '单据概览', '费用明细', '收款人', '标准 JSON'];
      if (timer) clearInterval(timer);
      activeTab = flow[index];
      render();
      timer = setInterval(() => {
        index += 1;
        if (index >= flow.length) { clearInterval(timer); timer = null; return; }
        activeTab = flow[index];
        if (activeTab === '运行中状态') selectedOrder = 1;
        if (activeTab === '费用明细' || activeTab === '收款人' || activeTab === '标准 JSON') selectedOrder = 0;
        render();
      }, 1700);
    }));
  }

  render();
})();
