"""
EPC 报销 Agent 配置
登录信息请通过环境变量设置，不要硬编码
  set EPC_USER=your_email@corp.netease.com
  set EPC_PASS=your_password
"""
import os

EPC_URL = "https://eplayer.nie.netease.com/#/app/epc/new/payment"
EPC_USER = os.getenv("EPC_USER", "")
EPC_PASS = os.getenv("EPC_PASS", "")

# Playwright 浏览器数据目录（复用已登录态，推荐）
# 设置后不需要账密登录；建议使用自动化专用目录，不要直接使用日常浏览器资料目录。
CHROME_USER_DATA = os.getenv("CHROME_USER_DATA", "")

# EPC 自动化默认使用 Microsoft Edge。若需切回 Playwright 自带 Chromium，设置为空即可。
BROWSER_CHANNEL = os.getenv("EPC_BROWSER_CHANNEL", "msedge").strip()

# 批次并发设置
# 共享一个已登录浏览器上下文；每张单使用独立标签页。默认最多同时填写 3 张。
# 可通过环境变量 EPC_BATCH_CONCURRENCY=1/2/3 临时降级。
try:
    BATCH_CONCURRENCY = max(1, min(3, int(os.getenv("EPC_BATCH_CONCURRENCY", "3"))))
except ValueError:
    BATCH_CONCURRENCY = 3
MAX_RETRY = 2              # 单据失败自动重试次数（预留，第 3 阶段启用）
SCREENSHOT_DIR = ""        # 截图目录；为空则使用脚本目录下的 screenshots/

# 超时设置（毫秒）
TIMEOUT = 30_000
SLOW_MO = 300  # 每步操作间隔，设 0 最快，调试时设 500+

# 报销明细 checkbox value 映射
REIMBURSEMENT_TYPE_MAP = {
    "礼金(小众/特殊)": "domestic",
    "国内玩家礼金(小众/特殊)": "domestic",
    "礼金(常规)": "domesticCommon",
    "国内玩家礼金(常规)": "domesticCommon",
    "问卷调研": "questionnaire",
    "国内问卷调研": "questionnaire",
    "兼职": "partTime",
    "国内兼职": "partTime",
    "其他": "other",
}

# 发包类型 radio value 映射
FANBAOTYPE_MAP = {
    "用户调研": "person",
    "用户研究": "person",
    "专家咨询": "expert",
    "游戏测评": "game",
}
