"""Validate historical quotes and render reports exclusively from attributable facts."""
import json
import math
from datetime import date


def valid_quote(row):
    try:
        date.fromisoformat(row['date'])
        o, h, l, c, v = (float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume'))
        return all(math.isfinite(x) for x in (o, h, l, c, v)) and v >= 0 and 0 < l <= min(o, c) <= max(o, c) <= h
    except (KeyError, TypeError, ValueError):
        return False


def quote_quality(rows):
    good = [r for r in rows if valid_quote(r)]
    return {'total': len(rows), 'valid': len(good), 'excluded': len(rows)-len(good),
            'status': 'verified' if good and len(good) == len(rows) else 'partial' if good else 'unavailable',
            'latest_valid_date': max((r['date'] for r in good), default=None),
            'volume_unit': 'unspecified', 'turnover_available': False}


def stock_facts(name, rows, news, reports, quality):
    facts = {'历史行情与资料': [], '资讯索引': [], '数据边界': []}
    if rows:
        first, last = rows[0], rows[-1]
        facts['历史行情与资料'].append(f"{name}最近可用收盘价为 {float(last['close']):.2f} 元，日期 {last['date']}。")
        if len(rows) > 1:
            change = (float(last['close']) / float(first['close']) - 1) * 100
            facts['历史行情与资料'].append(f"{first['date']} 至 {last['date']} 的有效记录收盘价变动 {change:+.2f}%。")
    else:
        facts['历史行情与资料'].append('行情校验未通过，价格与涨跌计算已暂停。')
    for item in reports[:2]:
        facts['历史行情与资料'].append(f"研报索引：{item.get('报告名称', '未命名')}（{item.get('机构', '机构未标注')}）。仅有标题，不能据此推断业绩。")
    for item in news[:3]:
        facts['资讯索引'].append(f"{item.get('发布时间', '日期未标注')}：{item.get('新闻标题', '标题缺失')}。")
    if not facts['资讯索引']:
        facts['资讯索引'].append('暂无可核对的资讯记录。')
    facts['数据边界'] = [f"共 {quality['total']} 条历史行情，{quality['excluded']} 条异常记录已排除。",
                          '成交量单位未标注，未提供成交额，不作单位换算或资金流向推断。',
                          '以上为历史资料，不代表实时行情，也不据此生成买卖指令。']
    return facts


def grounded_report(sections, generate):
    catalog = {heading: {f'{i}-{j}': fact for j, fact in enumerate(facts)}
               for i, (heading, facts) in enumerate(sections.items())}
    chosen = {heading: list(facts)[:3] for heading, facts in catalog.items()}
    status = 'verified_summary'
    try:
        raw = generate(catalog)
        selection = json.loads(raw)
        if set(selection) != set(catalog):
            raise ValueError('Unexpected report sections')
        for heading, ids in selection.items():
            if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(not isinstance(x, str) or x not in catalog[heading] for x in ids) or len(set(ids)) != len(ids):
                raise ValueError('Unverifiable report fact')
        chosen, status = selection, 'model_grounded'
    except (ValueError, TypeError, RuntimeError):
        pass
    # Quality boundaries must always appear, regardless of the model's selection.
    if '数据边界' in catalog:
        chosen['数据边界'] = list(catalog['数据边界'])
    return {'reply': '\n\n'.join('## '+heading+'\n\n'+'\n'.join('- '+catalog[heading][key] for key in ids)
                                 for heading, ids in chosen.items()),
            'grounding_status': status, 'sources': catalog}
