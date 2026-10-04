"""Read-only acceptance of the served analytics and navigation refresh.

Uses the existing private access configuration without printing its token.
All non-GET API requests are blocked and recorded: no bill, bank, goal, or
assistant mutation is permitted. Browser preferences use an isolated context.
Run with the Python environment containing playwright and an installed browser.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime/ui-refresh-review"
OUT.mkdir(parents=True, exist_ok=True)
CONFIG = json.loads((ROOT / ".runtime/remote-access.json").read_text())
BASE = f"http://{CONFIG['host']}:{CONFIG['port']}"
CHECKS: list[str] = []
LAYOUTS: list[dict] = []
ERRORS: list[str] = []
MUTATIONS: list[dict] = []
REQUESTS: list[str] = []
STARTED_AT = datetime.now(timezone.utc).isoformat()
FRONTEND = ROOT / "AI_accounting_agent/frontend/liquid-glass"
# Original portrait shell defined in tokens.css, shared with the home page.
ORIGINAL_APP_WIDTH = 430


def source_hashes():
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(FRONTEND.iterdir())
        if path.suffix in (".js", ".css", ".html")
    }


SOURCE_HASHES = source_hashes()


def check(message: str) -> None:
    CHECKS.append(message)
    print("PASS", message, flush=True)


def digits(text: str) -> str:
    return re.sub(r"[^\d.\-]", "", text)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True,
        executable_path=os.getenv("PLAYWRIGHT_EXECUTABLE_PATH"),
    )
    context = browser.new_context(viewport={"width": 393, "height": 852}, locale="zh-CN")

    def readonly(route):
        request = route.request
        parts = urlsplit(request.url)
        if parts.path.startswith("/api/"):
            REQUESTS.append(parts.path + ("?" + parts.query if parts.query else ""))
            if request.method != "GET":
                MUTATIONS.append({"method": request.method, "path": parts.path})
                route.abort("blockedbyclient")
                return
        route.continue_()

    context.route("**/*", readonly)
    page = context.new_page()
    page.on("pageerror", lambda error: ERRORS.append(str(error)))

    def ready():
        page.wait_for_function("document.querySelector('main') && !document.querySelector('main .loading')")
        expect(page.locator("main .error")).to_have_count(0)
        page.wait_for_timeout(120)

    def visit(route: str):
        page.goto(BASE + "/#/" + route, wait_until="networkidle")
        ready()
        expect(page.locator("#app")).to_have_attribute("data-route", route)

    def action(name: str, value: str | None = None):
        selector = f'[data-action="{name}"]'
        if value is not None:
            selector += f'[data-value="{value}"]'
        page.locator(selector).first.click()
        ready()

    def month(value="2026-09"):
        page.locator("#month-picker").fill(value)
        ready()
        expect(page.locator("#month-picker")).to_have_value(value)

    def set_custom(start: str, end: str):
        if not page.locator("#visual-range-form").count():
            action("visual-period", "自定义")
        page.locator("#visual-start").fill(start)
        page.locator("#visual-end").fill(end)
        page.locator('#visual-range-form button[type="submit"]').click()
        ready()

    def aggregate(start: str, end: str):
        response = context.request.get(
            BASE + f"/api/book/reports/aggregate?start_date={start}&end_date={end}"
        )
        assert response.status == 200
        return response.json()["custom"]

    def metrics(report):
        expect(page.locator(".analytics-totals .metric")).to_have_count(2)
        assert digits(page.locator(".balance-value").inner_text()) == f"{report['net']['current']:.2f}"
        for index, key in enumerate(("income_total", "expense_total")):
            value = page.locator(".analytics-totals .metric strong").nth(index).inner_text()
            assert digits(value) == f"{report['summary'][key]:.2f}", (key, value)

    failure = None
    try:
        page.goto(BASE + "/__open/" + CONFIG["token"], wait_until="networkidle")
        ready()
        before = context.request.get(BASE + "/api/book/health").json()["bill_count"]
        september = aggregate("2026-09-01", "2026-09-30")

        for width, height in ((393, 852), (360, 800), (768, 900), (1280, 900), (1920, 1080)):
            page.set_viewport_size({"width": width, "height": height})
            visit("home")
            home_shell = page.locator("#app").bounding_box()
            assert abs(home_shell["width"] - min(width, ORIGINAL_APP_WIDTH)) < 1, home_shell
            page.screenshot(path=str(OUT / f"final-home-{width}-viewport.png"))
            for route in ("visualization", "dashboard", "risk"):
                visit(route)
                if route == "visualization":
                    month()
                    metrics(september)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(180)
                layout = page.evaluate("""() => ({
                    route: document.querySelector('#app').dataset.route,
                    width: innerWidth, pageWidth: document.documentElement.scrollWidth,
                    height: document.documentElement.scrollHeight,
                    shell: (()=>{const e=document.querySelector('#app'), r=e.getBoundingClientRect();
                      return {width:r.width,left:r.left,right:r.right,maxWidth:getComputedStyle(e).maxWidth};})(),
                    cards: [...document.querySelectorAll('main .surface-card')].map(e=>{
                      const r=e.getBoundingClientRect();
                      return {className:e.className,top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width};
                    }),
                    overflowing: [...document.querySelectorAll('main > *, main .surface-card')]
                      .filter(e => e.getBoundingClientRect().right > innerWidth + 1 || e.getBoundingClientRect().left < -1)
                      .map(e => ({tag:e.tagName, className:e.className}))
                })""")
                assert layout["pageWidth"] <= width, layout
                assert not layout["overflowing"], layout
                shell = layout["shell"]
                assert shell["maxWidth"] == f"{ORIGINAL_APP_WIDTH}px", layout
                assert abs(shell["width"] - home_shell["width"]) < 1, layout
                assert abs(shell["width"] - min(width, ORIGINAL_APP_WIDTH)) < 1, layout
                assert abs(shell["left"] - (width - shell["width"]) / 2) < 1, layout
                cards = layout["cards"]
                assert len(cards) >= 2, layout
                for index, card in enumerate(cards):
                    assert card["left"] >= shell["left"] and card["right"] <= shell["right"], layout
                    assert abs(card["left"] - cards[0]["left"]) < 1, layout
                    assert abs(card["width"] - cards[0]["width"]) < 1, layout
                    if index:
                        assert card["top"] >= cards[index - 1]["bottom"] - 1, layout
                if route == "visualization":
                    layout["plot"] = page.evaluate("""() => {
                        const svg = document.querySelector('.cashflow-chart').getBoundingClientRect();
                        const grid = document.querySelector('.cashflow-chart .chart-grid line').getBoundingClientRect();
                        const dates = [...document.querySelectorAll('.cashflow-chart .chart-date')].map(e=>e.getBoundingClientRect().bottom);
                        return {svgWidth:svg.width, gridWidth:grid.width, gridRatio:grid.width/svg.width,
                          dateBottom:Math.max(...dates), navTop:document.querySelector('.tab-bar').getBoundingClientRect().top};
                    }""")
                    assert layout["plot"]["gridRatio"] >= 0.80, layout
                    if width in (360, 393):
                        assert layout["plot"]["dateBottom"] < layout["plot"]["navTop"] - 2, layout
                LAYOUTS.append(layout)
                page.screenshot(path=str(OUT / f"final-{route}-{width}-viewport.png"))
                if width in (393, 1280, 1920):
                    page.screenshot(path=str(OUT / f"final-{route}-{width}-full.png"), full_page=True)
        check("5 种宽度下图表、投资图表与风险页均和首页同宽，上限 430px；主要卡片保持竖版单列")
        check("15 个分析页面/视口组合无横溢；手机趋势日期轴完整可见，图形在窄卡片内正常铺开")

        page.set_viewport_size({"width": 393, "height": 852})
        visit("visualization")
        month()
        action("visual-type", "柱状")
        expect(page.locator(".cashflow-bar")).to_have_count(len(september["bars"]["labels"]) * 2)
        expect(page.locator(".cashflow-line")).to_have_count(0)
        first = page.locator(".cashflow-chart .chart-point").first
        first.focus()
        page.keyboard.press("Enter")
        expect(page.locator(".cashflow-container .chart-value")).to_contain_text("收入")
        action("visual-type", "折线")
        expect(page.locator(".cashflow-line")).to_have_count(2)
        expect(page.locator(".cashflow-bar")).to_have_count(0)
        for label, start, end in (
            ("周", "2026-08-31", "2026-09-06"),
            ("年", "2026-01-01", date.today().isoformat() if date.today().year == 2026 else "2026-12-31"),
            ("月", "2026-09-01", "2026-09-30"),
        ):
            action("visual-period", label)
            expect(page.locator(".analytics-date")).to_have_attribute("title", start + " — " + end)
            metrics(aggregate(start, end))
        check("折线/柱状保留逐日数据、键盘读数可用，周/月/年金额与实际接口一致")

        expect(page.locator(".donut-legend-row:visible")).to_have_count(5)
        page.locator(".donut-more > summary").click()
        expect(page.locator(".donut-legend-row:visible")).to_have_count(len(september["pie"]))
        page.locator(".donut-more > summary").click()
        action("visual-breakdown", "对比")
        expect(page.locator(".comparison-row:visible")).to_have_count(5)
        action("visual-breakdown", "占比")
        action("mask")
        expect(page.locator(".cashflow-chart")).to_have_count(0)
        expect(page.locator(".donut-chart")).to_have_count(0)
        expect(page.locator(".balance-value")).to_have_text("••••")
        expect(page.locator(".analytics-totals .metric strong").first).to_have_text("••••")
        action("mask")
        metrics(september)
        check("分类前五项与展开全部均可读，对比切换正常，金额隐藏同时移除图形")

        set_custom("2026-09-20", "2026-09-01")
        expect(page.locator("#visual-range-error")).to_contain_text("开始日期不能晚于结束日期")
        set_custom("1990-01-01", "1990-01-31")
        expect(page.locator(".analytics-trend")).to_contain_text("本期暂无收支数据")
        expect(page.locator(".cashflow-chart")).to_have_count(0)
        assert "NaN" not in page.locator("main").inner_text()
        assert "Infinity" not in page.locator("main").inner_text()
        set_custom("2026-09-01", "2026-09-10")
        custom = aggregate("2026-09-01", "2026-09-10")
        metrics(custom)
        check("自定义日期拒绝倒序，空区间完整空状态，金额与所选区间一致")

        category_link = page.locator("button.donut-legend-row").first
        category = category_link.get_attribute("data-value")
        category_link.scroll_into_view_if_needed()
        page.wait_for_timeout(650)
        before_scroll = page.evaluate("scrollY")
        category_link.click()
        ready()
        expect(page.locator("#app")).to_have_attribute("data-route", "ledger")
        expect(page.locator("main")).to_contain_text("2026-09-01 — 2026-09-10")
        assert page.locator(".bill-row").count() > 0
        for bill in page.locator(".bill-row").all():
            assert category in bill.inner_text(), bill.inner_text()
        action("back")
        expect(page.locator("#app")).to_have_attribute("data-route", "visualization")
        expect(page.locator("#visual-start")).to_have_value("2026-09-01")
        expect(page.locator("#visual-end")).to_have_value("2026-09-10")
        assert abs(page.evaluate("scrollY") - before_scroll) < 6, (before_scroll, page.evaluate("scrollY"))
        check("分类钻取只显示对应账本，左上返回保留日期、金额和原滚动位置")

        action("visual-insights")
        expect(page.locator("#app")).to_have_attribute("data-route", "insights")
        expect(page.locator("main .period-stamp.center")).to_have_text("2026-09-01 — 2026-09-10")
        assert digits(page.locator(".hero-amount").first.inner_text()) == f"{custom['net']['current']:.2f}"
        expect(page.locator(".tab-bar a[aria-current='page']")).to_have_attribute("href", "#/visualization")
        action("back")
        expect(page.locator("#visual-start")).to_have_value("2026-09-01")
        check("详细分析继承图表自定义区间，导航归属图表且可返回")

        visit("dashboard")
        action("research-holdings")
        expect(page.locator("#app")).to_have_attribute("data-route", "research")
        expect(page.locator("h1")).to_have_text("投资研究")
        expect(page.locator(".tab-bar a[aria-current='page']")).to_have_attribute("href", "#/investment")
        action("back")
        expect(page.locator("#app")).to_have_attribute("data-route", "dashboard")
        check("投资图表的历史持仓进入投资研究，返回与投资导航归属正确")

        after = context.request.get(BASE + "/api/book/health").json()["bill_count"]
        assert before == after, (before, after)
        assert not MUTATIONS, MUTATIONS
        assert not ERRORS, ERRORS
        assert source_hashes() == SOURCE_HASHES, "Frontend changed during browser acceptance; rerun after edits finish"
        check("全部浏览操作零非 GET API 请求、零脚本错误，账单数量保持不变")
    except Exception as error:
        failure = str(error).replace(CONFIG["token"], "[redacted]")
        page.screenshot(path=str(OUT / "final-failure.png"), full_page=True)
        raise
    finally:
        (OUT / "final-validation.json").write_text(json.dumps({
            "started_at": STARTED_AT, "finished_at": datetime.now(timezone.utc).isoformat(),
            "frontend_sha256": SOURCE_HASHES,
            "checks": CHECKS, "layouts": LAYOUTS, "errors": ERRORS,
            "non_get_api_requests": MUTATIONS, "api_get_requests": len(REQUESTS),
            "failure": failure,
        }, ensure_ascii=False, indent=2))
        browser.close()
