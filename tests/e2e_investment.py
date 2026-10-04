"""Read-only browser acceptance of ledger-connected investment navigation.

Production API writes are blocked. Boundary cases use isolated browser contexts
with mocked overview responses; profile saving and insufficient-balance review
are fulfilled locally, without live writes. Set PLAYWRIGHT_EXECUTABLE_PATH
when the Playwright default browser is not installed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "AI_accounting_agent/frontend/liquid-glass"
OUT = ROOT / ".runtime/investment-review"
OUT.mkdir(parents=True, exist_ok=True)
CONFIG = json.loads((ROOT / ".runtime/remote-access.json").read_text())
BASE = f"http://{CONFIG['host']}:{CONFIG['port']}"
REPORT = {"started_at": datetime.now(timezone.utc).isoformat(), "checks": [],
          "layouts": [], "javascript_errors": [], "blocked_production_writes": [],
          "isolated_mock_writes": [], "scenarios": [], "failure": None}


def hashes():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(FRONTEND.iterdir()) if path.suffix in (".js", ".css", ".html")}


REPORT["frontend_sha256"] = hashes()


def check(message):
    REPORT["checks"].append(message)
    print("PASS", message, flush=True)


def number(text):
    return re.sub(r"[^\d.\-]", "", text)


def money_text(minor):
    return f"{minor / 100:,.2f}"


def ready(page):
    page.wait_for_function("document.querySelector('main') && !document.querySelector('main .loading')")
    expect(page.locator("main > .error")).to_have_count(0)
    page.wait_for_timeout(120)


def visit(page, route):
    page.goto(BASE + "/#/" + route, wait_until="networkidle")
    ready(page)
    expect(page.locator("#app")).to_have_attribute("data-route", route)


def action(page, name, value=None):
    selector = f'[data-action="{name}"]'
    if value is not None:
        selector += f'[data-value="{value}"]'
    page.locator(selector).first.click()
    ready(page)


def month(page, value="2026-09"):
    page.locator("#month-picker").fill(value)
    ready(page)
    expect(page.locator("#month-picker")).to_have_value(value)


def metric(page, name, expected_minor):
    element = page.locator(f'[data-plan-metric="{name}"]')
    expect(element).to_have_count(1)
    actual = number(element.inner_text())
    assert actual == f"{expected_minor / 100:.2f}", (name, actual, expected_minor)


def get_json(context, path):
    response = context.request.get(BASE + path)
    assert response.status == 200, (path, response.status)
    return response.json()


def aggregate(context, start="2026-09-01", end="2026-09-30"):
    return get_json(context, f"/api/book/reports/aggregate?start_date={start}&end_date={end}")["custom"]


def month_metrics(page, report):
    metric(page, "month-net", round(report["net"]["current"] * 100))
    metric(page, "month-income", round(report["summary"]["income_total"] * 100))
    metric(page, "month-expense", round(report["summary"]["expense_total"] * 100))


def context_page(browser, scenario=None, review_error=None, profile_save=False):
    context = browser.new_context(viewport={"width": 393, "height": 852}, locale="zh-CN")

    def intercept(route):
        request = route.request
        path = urlsplit(request.url).path
        if path.startswith("/api/") and request.method != "GET":
            if profile_save and scenario is not None and path == "/api/book/finance/profile" and request.method == "POST":
                data = request.post_data_json
                assert data["ledger_scope"] == scenario["profile"]["ledger_scope"]
                scenario["profile"].update(data)
                REPORT["isolated_mock_writes"].append({"method": request.method, "path": path,
                    "forwarded": False, "status": 200, "ledger_scope": data["ledger_scope"], "stage": data["stage"]})
                route.fulfill(status=200, content_type="application/json", body=json.dumps(scenario, ensure_ascii=False))
            elif review_error and path == "/api/book/finance/operations/review" and request.method == "POST":
                REPORT["isolated_mock_writes"].append({"method": request.method, "path": path,
                                                      "forwarded": False, "status": 400})
                route.fulfill(status=400, content_type="application/json",
                              body=json.dumps({"detail": review_error}, ensure_ascii=False))
            else:
                REPORT["blocked_production_writes"].append({"method": request.method, "path": path})
                route.abort("blockedbyclient")
            return
        if scenario is not None and path == "/api/book/finance/overview":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(scenario, ensure_ascii=False))
            return
        route.continue_()

    context.route("**/*", intercept)
    page = context.new_page()
    page.on("pageerror", lambda error: REPORT["javascript_errors"].append(str(error)))
    page.goto(BASE + "/__open/" + CONFIG["token"], wait_until="networkidle")
    ready(page)
    return context, page


def cash_sentinel(snapshot):
    fixture = copy.deepcopy(snapshot)
    fixture["cash_minor"] = 987_654_321
    fixture["assets_minor"] = fixture["cash_minor"] + sum(p["holding_minor"] for p in fixture["products"])
    return fixture


def fixture_goal(snapshot, *, target=0, recorded=0, months=12):
    """Make an explicit overview fixture; backend formula tests run separately."""
    fixture = cash_sentinel(snapshot)
    available = fixture["planning"]["monthly"]["available_minor"]
    remaining = max(0, target - recorded)
    required = (remaining + months - 1) // months
    fixture["profile"].update(goal_name="旅行目标", goal_amount=f"{target/100:.2f}",
                              goal_recorded_amount=f"{recorded/100:.2f}", goal_months=months)
    fixture["planning"]["goal"].update(target_minor=target, recorded_minor=recorded,
        remaining_minor=remaining, months=months, monthly_required_minor=required,
        monthly_allocated_minor=min(available, required), shortfall_minor=max(0, required-available),
        feasible=required <= available)
    fixture["planning"]["after_goal_minor"] = max(0, available-required)
    return fixture


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, executable_path=os.getenv("PLAYWRIGHT_EXECUTABLE_PATH"))
    context = None
    page = None
    try:
        context, page = context_page(browser)
        before_health = get_json(context, "/api/book/health")
        before_finance = get_json(context, "/api/book/finance/overview")
        historical = get_json(context, "/api/trader/trader/portfolio")
        assert before_finance["planning"]["bank_effect"] is False
        assert before_finance["planning"]["basis"]["kind"] == "observed_monthly_average"
        REPORT["before"] = {"bill_count": before_health["bill_count"], "cash_minor": before_finance["cash_minor"],
                            "assets_minor": before_finance["assets_minor"], "operations": len(before_finance["operations"])}

        for width, height in ((360, 800), (393, 852), (1280, 900)):
            page.set_viewport_size({"width": width, "height": height})
            for route in ("investment", "research", "holdings"):
                visit(page, route)
                expect(page.locator('.tab-bar a[href="#/investment"]')).to_have_text("投资")
                expect(page.locator('#investment-sections button[aria-current="page"]')).to_have_attribute("data-value", route)
                layout = page.evaluate("""() => {
                    const app=document.querySelector('#app'), r=app.getBoundingClientRect();
                    return {route:app.dataset.route,viewport:innerWidth,shellWidth:r.width,left:r.left,
                      scrollWidth:document.documentElement.scrollWidth,maxWidth:getComputedStyle(app).maxWidth};
                }""")
                assert layout["scrollWidth"] <= width and layout["maxWidth"] == "430px", layout
                assert abs(layout["shellWidth"] - min(430, width)) < 1, layout
                assert abs(layout["left"] - (width - layout["shellWidth"]) / 2) < 1, layout
                REPORT["layouts"].append(layout)
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(OUT / f"{route}-{width}-viewport.png"))
                if width == 393:
                    page.screenshot(path=str(OUT / f"{route}-{width}-full.png"), full_page=True)
        check("投资三分段与底部投资导航一致；360/393/1280 均保留 430px 竖版上限")

        page.set_viewport_size({"width": 393, "height": 852})
        visit(page, "investment")
        for selected, start, end in (("2026-09", "2026-09-01", "2026-09-30"),
                                     ("2026-08", "2026-08-01", "2026-08-31")):
            month(page, selected)
            month_metrics(page, aggregate(context, start, end))
            metric(page, "after-goal", before_finance["planning"]["after_goal_minor"])
        check("规划首卡随月份与账本收支完全一致；历史月均规划与当月净额分别展示")

        month(page)
        september = aggregate(context)
        action(page, "investment-ledger")
        expect(page.locator("#app")).to_have_attribute("data-route", "ledger")
        expect(page.locator("main")).to_contain_text("2026-09-01 — 2026-09-30")
        action(page, "back")
        expect(page.locator("#app")).to_have_attribute("data-route", "investment")
        expect(page.locator("#month-picker")).to_have_value("2026-09")
        month_metrics(page, september)
        action(page, "investment-chart")
        expect(page.locator("#app")).to_have_attribute("data-route", "visualization")
        expect(page.locator(".analytics-date")).to_have_attribute("title", "2026-09-01 — 2026-09-30")
        action(page, "back")
        expect(page.locator("#app")).to_have_attribute("data-route", "investment")
        month_metrics(page, september)
        basis = before_finance["planning"]["basis"]
        page.locator("details.planning-basis > summary").click()
        expect(page.locator("details.planning-basis")).to_contain_text(basis["start_date"])
        expect(page.locator("details.planning-basis")).to_contain_text(basis["end_date"])
        action(page, "investment-basis-ledger")
        expect(page.locator("#app")).to_have_attribute("data-route", "ledger")
        expect(page.locator("main")).to_contain_text(basis["start_date"] + " — " + basis["end_date"])
        action(page, "back")
        expect(page.locator("#month-picker")).to_have_value("2026-09")
        action(page, "journey-profile")
        expect(page.locator("#app")).to_have_attribute("data-route", "profile")
        action(page, "back")
        expect(page.locator("#app")).to_have_attribute("data-route", "investment")
        expect(page.locator("#month-picker")).to_have_value("2026-09")
        check("当月账单、收支图表及历史规划依据可追溯，返回后所选月份保持一致")

        action(page, "investment-section", "research")
        expect(page.locator("#app")).to_have_attribute("data-route", "research")
        expect(page.locator('[data-action="investment-tab"][data-value="自选"]')).to_have_attribute("aria-selected", "true")
        expect(page.locator(".simulated-assets")).to_have_count(0)
        expect(page.locator(".planning-summary")).to_have_count(0)
        action(page, "investment-tab", "持仓")
        expect(page.locator("main")).to_contain_text("历史")
        assert money_text(round(historical["summary"]["total_balance"] * 100)) in page.locator("main").inner_text()
        holding = historical["holdings"][0]
        page.locator('[data-action="research-holding"]').first.click()
        expect(page.locator("#sheet[open]")).to_be_visible()
        expect(page.locator("#sheet")).to_contain_text(holding["name"])
        expect(page.locator("#sheet")).to_contain_text(holding["code"])
        assert money_text(round(holding["market_value"] * 100)) in page.locator("#sheet").inner_text()
        expect(page.locator("#sheet")).to_contain_text("历史快照")
        action(page, "close-sheet")
        expect(page.locator("#app")).to_have_attribute("data-route", "research")
        action(page, "investment-section", "holdings")
        expect(page.locator(".simulated-assets")).to_have_count(1)
        assert money_text(before_finance["assets_minor"]) in page.locator(".simulated-assets").inner_text()
        assert money_text(round(historical["summary"]["total_balance"] * 100)) not in page.locator("main").inner_text()
        action(page, "journey-compare")
        expect(page.locator("#app")).to_have_attribute("data-route", "compare")
        action(page, "journey-profile")
        expect(page.locator("#app")).to_have_attribute("data-route", "profile")
        action(page, "back")
        expect(page.locator("#app")).to_have_attribute("data-route", "compare")
        action(page, "back")
        expect(page.locator("#app")).to_have_attribute("data-route", "holdings")
        check("自选研究、历史组合与模拟持仓各自独立，历史资产未混入模拟账户")
        check("历史持仓详情显示对应证券与快照，方案比较/计划编辑均能返回实际来源")

        action(page, "investment-section", "investment")
        action(page, "mask")
        expect(page.locator('[data-plan-metric="month-net"]')).to_have_text("••••")
        expect(page.locator('[data-plan-metric="after-goal"]')).to_have_text("••••")
        action(page, "investment-section", "holdings")
        assert money_text(before_finance["cash_minor"]) not in page.locator(".simulated-assets").inner_text()
        action(page, "investment-section", "investment")
        action(page, "mask")
        month_metrics(page, september)
        check("金额隐藏跨规划与模拟持仓生效，恢复后数据仍与同月账本一致")

        saved_profile = copy.deepcopy(before_finance)
        scenario_context, scenario_page = context_page(browser, saved_profile, profile_save=True)
        try:
            visit(scenario_page, "investment")
            month(scenario_page, "2026-08")
            august = aggregate(scenario_context, "2026-08-01", "2026-08-31")
            month_metrics(scenario_page, august)
            action(scenario_page, "journey-profile")
            expect(scenario_page.locator("#app")).to_have_attribute("data-route", "profile")
            expect(scenario_page.locator("#finance-scope")).to_have_value(before_finance["profile"]["ledger_scope"])
            stage = "early_career" if saved_profile["profile"]["stage"] == "student" else "student"
            scenario_page.locator("#finance-stage").select_option(stage)
            scenario_page.locator('#finance-profile-form button[type="submit"]').click()
            expect(scenario_page.locator("#app")).to_have_attribute("data-route", "investment")
            ready(scenario_page)
            expect(scenario_page.locator("#month-picker")).to_have_value("2026-08")
            month_metrics(scenario_page, august)
            assert saved_profile["profile"]["stage"] == stage
            assert saved_profile["profile"]["ledger_scope"] == before_finance["profile"]["ledger_scope"]
            action(scenario_page, "journey-profile")
            expect(scenario_page.locator("#finance-stage")).to_have_value(stage)
            action(scenario_page, "back")
            expect(scenario_page.locator("#month-picker")).to_have_value("2026-08")
            month_metrics(scenario_page, august)
            scenario_page.screenshot(path=str(OUT / "fixture-profile-save-preserves-month.png"), full_page=True)
            REPORT["scenarios"].append({"name": "save_profile_preserves_non_latest_month", "source": "isolated_overview_and_profile_fixture", "passed": True})
        finally:
            scenario_context.close()
        check("同一账本保存阶段后保留所选 8 月，返回投资的净额仍与 8 月账本一致；保存仅本地 mock")

        completed = fixture_goal(before_finance, target=100_000, recorded=100_000)
        scenario_context, scenario_page = context_page(browser, completed)
        try:
            visit(scenario_page, "investment")
            month(scenario_page)
            month_metrics(scenario_page, september)
            metric(scenario_page, "after-goal", completed["planning"]["monthly"]["available_minor"])
            goal_card = scenario_page.locator(".investment-goal")
            for label in ("目标剩余", "每月计划"):
                actual = goal_card.locator("dt", has_text=label).locator("..").locator("dd").inner_text()
                assert number(actual) == "0.00", (label, actual)
            expect(goal_card.locator(".progress-bar span")).to_have_attribute("style", "width:100%")
            assert money_text(completed["cash_minor"]) not in scenario_page.locator("main").inner_text()
            assert money_text(completed["assets_minor"]) not in scenario_page.locator("main").inner_text()
            scenario_page.screenshot(path=str(OUT / "fixture-goal-complete.png"), full_page=True)
            REPORT["scenarios"].append({"name": "completed_goal", "source": "isolated_overview_fixture", "passed": True})
        finally:
            scenario_context.close()
        check("已完成目标每月预留归零，规划不混入独立模拟现金或历史资产")

        missing = fixture_goal(before_finance)
        missing["planning"]["basis"]["observed_months"] = 0
        missing["cashflow"]["observed_months"] = 0
        for key in missing["planning"]["monthly"]:
            missing["planning"]["monthly"][key] = 0
        missing["planning"]["after_goal_minor"] = 0
        missing["planning"]["emergency"]["target_minor"] = 0
        scenario_context, scenario_page = context_page(browser, missing)
        try:
            visit(scenario_page, "investment")
            expect(scenario_page.locator('[data-plan-metric="after-goal"]')).to_have_text("—")
            expect(scenario_page.locator('.funding-plan [data-action="statement-import"]')).to_have_text("先补充账单")
            action(scenario_page, "statement-import")
            expect(scenario_page.locator("#app")).to_have_attribute("data-route", "import")
            action(scenario_page, "back")
            expect(scenario_page.locator("#app")).to_have_attribute("data-route", "investment")
            REPORT["scenarios"].append({"name": "missing_complete_history", "source": "isolated_overview_fixture", "passed": True})
        finally:
            scenario_context.close()
        check("缺少完整月历史时不生成可用金额，补账入口与返回可用")

        negative = fixture_goal(before_finance)
        negative["planning"]["basis"]["observed_months"] = 6
        negative["cashflow"].update(observed_months=6, monthly_income_minor=120_000,
            monthly_expense_minor=200_000, monthly_gross_expense_minor=200_000, monthly_net_minor=-80_000)
        negative["planning"]["monthly"].update(income_minor=120_000, net_expense_minor=200_000,
            gross_expense_minor=200_000, net_minor=-80_000, commitment_extra_minor=0, available_minor=0)
        negative["planning"]["after_goal_minor"] = 0
        scenario_context, scenario_page = context_page(browser, negative)
        try:
            visit(scenario_page, "investment")
            metric(scenario_page, "after-goal", 0)
            expect(scenario_page.locator('.funding-plan [data-action="investment-basis-ledger"]:visible')).to_have_text("先平衡收支")
            historical_net = scenario_page.locator(".funding-plan dt", has_text="月均结余").locator("..").locator("dd")
            assert number(historical_net.inner_text()) == "-800.00"
            assert money_text(negative["cash_minor"]) not in scenario_page.locator("main").inner_text()
            REPORT["scenarios"].append({"name": "negative_historical_net", "source": "isolated_overview_fixture", "passed": True})
        finally:
            scenario_context.close()

        gap = fixture_goal(before_finance, target=240_000, months=2)
        gap["planning"]["monthly"].update(net_minor=100_000, commitment_extra_minor=0, available_minor=100_000)
        gap["planning"]["goal"].update(monthly_allocated_minor=100_000, shortfall_minor=20_000, feasible=False)
        gap["planning"]["after_goal_minor"] = 0
        scenario_context, scenario_page = context_page(browser, gap)
        try:
            visit(scenario_page, "investment")
            metric(scenario_page, "after-goal", 0)
            metric(scenario_page, "goal-shortfall", 20_000)
            expect(scenario_page.locator('.funding-plan .primary[data-action="journey-profile"]')).to_have_text("调整目标计划")
            REPORT["scenarios"].append({"name": "goal_shortfall", "source": "isolated_overview_fixture", "passed": True})
        finally:
            scenario_context.close()
        check("负结余与目标缺口显示为待调整资金安排，不被模拟余额掩盖")

        no_cash = copy.deepcopy(before_finance)
        no_cash["cash_minor"] = 0
        no_cash["investable_minor"] = 0
        no_cash["products"][0]["holding_minor"] = 100_000
        no_cash["assets_minor"] = sum(p["holding_minor"] for p in no_cash["products"])
        scenario_context, scenario_page = context_page(browser, no_cash)
        try:
            visit(scenario_page, "holdings")
            purchases = scenario_page.locator('[data-action="journey-subscribe"]')
            assert purchases.count() == len(no_cash["products"])
            for button in purchases.all():
                expect(button).to_be_disabled()
            expect(scenario_page.locator(".holdings-products")).to_contain_text("当前没有可用余额")
            redemption = scenario_page.locator('[data-action="journey-redeem"]').first
            expect(redemption).to_be_enabled()
            redemption.click()
            expect(scenario_page.locator("#finance-operation-form")).to_be_visible()
            action(scenario_page, "close-sheet")
            scenario_page.screenshot(path=str(OUT / "fixture-no-cash.png"), full_page=True)
            REPORT["scenarios"].append({"name": "zero_cash_with_existing_holding", "source": "isolated_overview_fixture", "passed": True})
        finally:
            scenario_context.close()
        check("模拟余额不足时禁用申购，已有持仓仍能进入赎回核对，未提交资金操作")

        insufficient = copy.deepcopy(before_finance)
        insufficient["cash_minor"] = 100
        insufficient["products"][0]["eligible"] = True
        insufficient["assets_minor"] = insufficient["cash_minor"] + sum(p["holding_minor"] for p in insufficient["products"])
        scenario_context, scenario_page = context_page(browser, insufficient, review_error="可用余额不足")
        try:
            visit(scenario_page, "holdings")
            scenario_page.locator('[data-action="journey-subscribe"][data-product="reserve"]').click()
            scenario_page.locator("#finance-amount").fill("2.00")
            scenario_page.locator('#finance-operation-form button[type="submit"]').click()
            expect(scenario_page.locator("#finance-operation-error")).to_contain_text("可用余额不足")
            expect(scenario_page.locator("#finance-confirm-form")).to_have_count(0)
            REPORT["scenarios"].append({"name": "insufficient_balance_review", "source": "isolated_overview_and_review_fixture", "passed": True})
        finally:
            scenario_context.close()
        check("余额不足复核错误在原表单展示；该 POST 完全由浏览器 mock 响应、未发送到银行")

        after_health = get_json(context, "/api/book/health")
        after_finance = get_json(context, "/api/book/finance/overview")
        REPORT["after"] = {"bill_count": after_health["bill_count"], "cash_minor": after_finance["cash_minor"],
                           "assets_minor": after_finance["assets_minor"], "operations": len(after_finance["operations"])}
        assert REPORT["before"] == REPORT["after"], (REPORT["before"], REPORT["after"])
        assert not REPORT["javascript_errors"], REPORT["javascript_errors"]
        assert not REPORT["blocked_production_writes"], REPORT["blocked_production_writes"]
        assert REPORT["frontend_sha256"] == hashes(), "Frontend changed during acceptance; rerun when edits finish"
        check("真实账本、模拟银行余额和操作数量不变；无生产写请求及脚本错误")
    except Exception as error:
        REPORT["failure"] = str(error).replace(CONFIG["token"], "[redacted]")
        if page is not None:
            page.screenshot(path=str(OUT / "failure.png"), full_page=True)
        raise
    finally:
        REPORT["finished_at"] = datetime.now(timezone.utc).isoformat()
        (OUT / "results.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))
        browser.close()
