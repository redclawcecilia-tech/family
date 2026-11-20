// family 报告页渲染自检：无第三方依赖，Node.js 直接运行。
// 用 DOM 桩执行页面内联脚本，并核验关键数字与图表确实渲染出来。
const fs = require('fs');

const file = process.argv[2];
if (!file) {
  console.error('用法: node render_check.js <index.html>');
  process.exit(2);
}

const fail = message => {
  console.error(`RENDER CHECK FAILED: ${message}`);
  process.exit(1);
};

let html;
try {
  html = fs.readFileSync(file, 'utf8');
} catch (error) {
  fail(error.message);
}

const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
if (!scripts.length) fail('页面没有可执行的内联脚本');

const elements = {};
const document = {
  getElementById: id => (elements[id] ||= {
    textContent: '', innerHTML: '', className: '', style: {},
  }),
};
const location = { protocol: 'http:', href: 'http://118.196.102.49:8080/' };
const window = {};

try {
  eval(scripts.join('\n'));
} catch (error) {
  console.error(error.stack || error.message);
  fail(`页面脚本执行异常: ${error.message}`);
}

const valueOf = id => {
  const element = elements[id];
  return element ? String(element.textContent || element.innerHTML || '').trim() : '';
};
const required = [
  'reportDate', 'currentValue', 'shares', 'latestNav', 'totalReturn',
  'heroProfit', 'afterFee', 'feeEstimate', 'buyMarker', 'navLegend', 'metaDate',
];
const missing = required.filter(id => !valueOf(id));
if (missing.length) fail(`关键内容未渲染: ${missing.join(', ')}`);

for (const id of ['navChart', 'monthlyChart']) {
  if (valueOf(id).length < 100) fail(`${id} 图表为空或内容不完整`);
}

const renderedText = required.concat(['navChart', 'monthlyChart']).map(valueOf).join('\n');
if (/\b(?:NaN|Infinity|undefined)\b/.test(renderedText)) {
  fail('渲染结果包含 NaN、Infinity 或 undefined');
}

console.log('RENDER CHECK OK');
console.log(`reportDate=${valueOf('reportDate')}`);
console.log(`currentValue=${valueOf('currentValue')}`);
console.log(`totalReturn=${valueOf('totalReturn')}`);
console.log(`navChart=${valueOf('navChart').length} chars`);
console.log(`monthlyChart=${valueOf('monthlyChart').length} chars`);
