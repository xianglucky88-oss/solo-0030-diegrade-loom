import {request} from './api.js';
import {byId, options} from './dom.js';
import {flash} from './status.js';

export async function loadRules() {
  const select = byId('wafer-form').elements.rule;
  const values = await request('/rules');
  options(select, values.map(rule => [`${rule.name}:${rule.version}`, `${rule.name} · 第 ${rule.version} 版`]), select.value);
}

function arrayJSON(text, label) {
  try {
    const value = JSON.parse(text);
    if (Array.isArray(value)) return value;
  } catch { /* 同一提示覆盖 JSON 语法和顶层类型错误。 */ }
  throw new Error(`${label}须为 JSON 数组`);
}

export function bindCatalog(action, open) {
  byId('rule-form').addEventListener('submit', action(async event => {
    const data = new FormData(event.target);
    const payload = Object.fromEntries([...data].map(([key, value]) => [key, key === 'name' ? value : Number(value)]));
    await request('/rules', payload);
    await loadRules();
    flash(`规则 ${payload.name} 第 ${payload.version} 版已登记`);
  }));
  byId('wafer-form').addEventListener('submit', action(async event => {
    const data = new FormData(event.target);
    const [rule, version] = String(data.get('rule') || '').split(':');
    if (!rule) throw new Error('请先登记并选择规则版本');
    const payload = {id: data.get('id'), rule, rule_version: Number(version),
      spans: arrayJSON(data.get('spans'), '物理行段'), reserved: arrayJSON(data.get('reserved'), '保留格点')};
    await request('/wafers', payload);
    await open(payload.id);
    flash(`晶圆 ${payload.id} 已登记`);
  }));
}
