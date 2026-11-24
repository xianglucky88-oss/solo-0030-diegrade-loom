import {byId, node} from './dom.js';
import {labels} from './status.js';

function svgNode(tag, attrs) {
  const result = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) result.setAttribute(key, value);
  return result;
}

export function renderMap(data, selected, onPick) {
  const target = byId('map');
  const caption = byId('map-caption');
  if (!data) {
    target.replaceChildren(node('p', '未登记晶圆'));
    caption.textContent = '';
    return;
  }
  const xs = data.cells.map(c => c.site[0]);
  const ys = data.cells.map(c => -c.site[1]);
  const [left, top] = [Math.min(...xs) - 1, Math.min(...ys) - 1];
  const [width, height] = [Math.max(...xs) - left + 1, Math.max(...ys) - top + 1];
  const svg = svgNode('svg', {viewBox: `${left} ${top} ${width} ${height}`, role: 'img', 'aria-label': `${data.wafer.id} 物理格点图`});
  svg.append(svgNode('line', {x1: left, y1: 0, x2: left + width, y2: 0, class: 'axis'}),
    svgNode('line', {x1: 0, y1: top, x2: 0, y2: top + height, class: 'axis'}));
  for (const cell of data.cells) {
    const [x, y] = cell.site;
    const active = selected?.[0] === x && selected?.[1] === y;
    const rect = svgNode('rect', {x: x - .42, y: -y - .42, width: .84, height: .84, rx: .08,
      class: `die ${cell.state}${active ? ' selected' : ''}`});
    const title = svgNode('title', {});
    const detail = cell.conditions
      ? ` · ${Object.entries(cell.conditions).map(([name, part]) => `${name} ${labels[part.state]}`).join(' / ')}`
      : '';
    title.textContent = `(${x}, ${y}) · ${labels[cell.state]}${detail}`;
    rect.append(title);
    rect.addEventListener('click', () => onPick(cell.site));
    svg.append(rect);
  }
  target.replaceChildren(svg);
  caption.textContent = `${data.cells.length} 个物理点 · ${data.counts.reserved ?? 0} 个保留点`;
}
