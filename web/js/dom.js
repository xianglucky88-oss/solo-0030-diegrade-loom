export const byId = id => document.getElementById(id);

export function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

export function options(select, values, selected) {
  select.replaceChildren(...values.map(([value, text]) => new Option(text, value)));
  if (values.some(([value]) => String(value) === String(selected))) select.value = selected;
}

export function table(headers, rows) {
  const result = node('table', undefined, 'data');
  const headings = result.createTHead().insertRow();
  for (const header of headers) headings.append(node('th', header));
  const body = result.createTBody();
  for (const row of rows) {
    const line = body.insertRow();
    for (const value of row) line.insertCell().textContent = value;
  }
  return result;
}
