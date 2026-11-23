import {request, waferPath} from './api.js';
import {byId, node, table} from './dom.js';
import {flash, labels} from './status.js';

export function clearPreview() { byId('preview').replaceChildren(); }

export function bindBatch(action, current, reload) {
  const form = byId('batch-form');
  let changed = 0;
  form.addEventListener('input', () => { ++changed; clearPreview(); });
  byId('file').addEventListener('change', action(async event => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 960000) throw new Error('探针表文件过大');
    const csv = await file.text();
    if (event.target.files[0] === file) {
      form.elements.csv.value = csv;
      ++changed;
      clearPreview();
    }
  }));
  form.addEventListener('submit', action(async event => {
    const mode = event.submitter?.value || 'preview';
    const inputVersion = ++changed;
    const values = new FormData(form);
    const {wafer, revision} = current();
    const id = wafer.id;
    const batch = {id: values.get('id'), kind: values.get('kind'), csv: values.get('csv'),
      frame: {offset: [Number(values.get('dx')), Number(values.get('dy'))], turn: Number(values.get('turn')), mirror: values.has('mirror')}};
    clearPreview();
    const outcome = await request(waferPath(id, `/${mode}`), {expected: revision, batch});
    if (mode === 'batches') {
      flash(outcome.duplicate ? `批次 ${batch.id} 已收录` : `批次 ${batch.id} 已录入第 ${outcome.revision} 代`);
      await reload();
      return;
    }
    const now = current();
    if (now.wafer.id !== id || now.revision !== revision || inputVersion !== changed) return;
    const output = byId('preview');
    output.append(node('p', outcome.duplicate ? '相同批次已经导入' : `预检通过：${outcome.changes.length} 颗，未写入`));
    if (outcome.changes.length) output.append(table(['物理格点', '拟写入状态'], outcome.changes.map(c => [c.site.join(', '), labels[c.state]])));
  }));
}
