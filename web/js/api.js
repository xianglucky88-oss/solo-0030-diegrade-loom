const prefix = '/api/diegrade';

export function waferPath(id, suffix = '') {
  return `/wafers/${encodeURIComponent(id)}${suffix}`;
}

export async function request(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  };
  let response;
  try {
    response = await fetch(prefix + path, options);
  } catch {
    throw new Error('无法连接晶格分档台');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '操作失败');
  return data;
}

export function downloadURL(id, revision, format) {
  return prefix + waferPath(id, `/releases/${revision}/${format}`);
}

export function assetURL(id, suffix) {
  return prefix + waferPath(id, suffix);
}
