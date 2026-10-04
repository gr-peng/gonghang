"""Shared categories and extraction contract used by training and the API."""
import re
EXPENSE_CATEGORIES = ["餐饮", "出行", "购物", "生活缴费", "娱乐", "住房", "医疗健康", "教育学习", "保险", "人情往来", "旅行", "其他"]
INCOME_CATEGORIES = ["工资", "奖金", "副业", "理财收益", "退款", "其他收入"]


def multiple_bill_reason(text):
    """Do not let a valid single-object model response hide a second transaction."""
    if re.search(r'(?:[两二三四五六七八九十2-9]|多|几)\s*笔|分别(?:记|记录)|记(?:成|下)两', text):
        return '检测到多笔收支，请分开描述并逐笔核对；本次没有生成或保存任何账单。'
    number = r'(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万点]+)'
    clauses = re.split(r'[，,；;。\n]|(?:另外|还有|然后)', text)
    actions = r'午饭|晚饭|早餐|午餐|晚餐|打车|地铁|公交|买|购买|交|付|花|工资|收入|转给|转来'
    priced = [part for part in clauses if re.search(actions, part) and re.search(number + r'\s*(?:元|块|块钱|[，,；;。\s]*$)', part)]
    distinct = len(re.findall(r'(?:午饭|晚饭|早餐|午餐|晚餐|打车|地铁|公交|工资|收入|买[^\d，,；;]{0,12})\s*(?:花了?|支付了?|是|为)?\s*' + number + r'\s*(?:元|块)', text))
    if (len(priced) > 1 or distinct > 1) and not re.search(r'退款|退了|退回|找零|优惠|折扣|原价|合计|一共|AA|承担', text, re.I):
        return '描述中有不止一笔金额，请分开输入每笔收支，避免漏记。'
    return None


def explicit_payment(text):
    """Normalize only payment methods explicitly present in the user's text."""
    aliases = {"微信支付": ["微信"], "支付宝": ["支付宝"], "银行卡": ["银行卡", "银行转账"],
               "信用卡": ["信用卡"], "现金": ["现金"]}
    methods = [name for name, words in aliases.items() if any(word in text for word in words)]
    return methods[0] if len(methods) == 1 else "其他" if methods else ""


def extraction_prompt(reference_date):
    return (
        f"今天是 {reference_date}。提取用户描述中的一笔账单，只输出 JSON。"
        "字段：event_date(YYYY-MM-DD), category, type(expense/income), amount(正数，最多两位小数), "
        "currency(CNY), description(简短保留用途), payment_method(未说明则空字符串)。"
        f"支出分类：{'/'.join(EXPENSE_CATEGORIES)}。收入分类：{'/'.join(INCOME_CATEGORIES)}。"
        "只依据输入，不编造金额、日期或支付方式；未说明日期按今天。退款按现金流计为收入/退款，不计为工资或理财收益。"
        "内部转账、借款本金、投资本金买卖和信用卡还款不直接计为收支，返回"
        '{"needs_clarification":true,"reason":"说明需要核对的信息"}。'
        "缺金额、多笔交易、无法确定币种或日期时也返回 needs_clarification 和 reason，等待确认，不自动入账。"
    )
