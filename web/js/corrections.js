import {request, waferPath} from './api.js';
import {byId, table} from './dom.js';
import {flash, labels} from './status.js';

export function bindCorrections(action, current, reload) {
  const form = byId('correct-form');
  form.addEventListener('submit', action(async event => {
    event.preventDefault();
    const output = byId('correction-output');
    output.replaceChildren();
    const values = new FormData(form);
    const {wafer, revision} = current();
    const outcome = await request(waferPath(wafer.id, '/corrections'), {
      expected: revision,
      id: values.get('id'),
      batch: values.get('batch'),
      reason: values.get('reason'),
      frame: {offset: [Number(values.get('dx')), Number(values.get('dy'))],
        turn: Number(values.get('turn')), mirror: values.has('mirror')},
    });
    await reload();
    if (outcome.duplicate) {
      flash(`更正 ${values.get('id')} 已收录`);
      return;
    }
    flash(`已登记更正，进入第 ${outcome.revision} 代`);
    if (outcome.changes.length) {
      output.append(table(['物理格点', '现场状态', '复测链'],
        outcome.changes.map(c => [c.site.join(', '), labels[c.state], c.reopened ? '旧链保留作历史，须重新补测' : '沿用'])));
    }
  }));
}
