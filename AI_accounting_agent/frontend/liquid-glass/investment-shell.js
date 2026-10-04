const sections = [['wealth','资产总览'],['investment','资金规划'],['research','投资研究'],['holdings','模拟持仓']];

export function investmentNavigation(active) {
  return `<nav id="investment-sections" class="investment-sections segmented glass" aria-label="投资模块">${sections.map(([route,label])=>`<button type="button" data-action="investment-section" data-value="${route}" ${route===active?'aria-current="page"':''}>${label}</button>`).join('')}</nav>`;
}
