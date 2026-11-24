import csv
import io
import re

from .errors import DomainError
from .validation import fields, identifier, integer

HEADER = ['x', 'y', 'contact', 'leak_na', 'margin_mv', 'parent']


def read_number(text, low, high, label):
    if not re.fullmatch(r'-?[0-9]{1,8}', text):
        raise DomainError('csv_number', f'{label}格式错误')
    return integer(int(text), low, high, label)


def read_row(row, kind):
    if len(row) != len(HEADER):
        raise DomainError('csv_row', '列数不正确')
    x, y, contact, leak, margin, parent = row
    if contact not in ('ok', 'open') or (contact == 'open' and (leak or margin)):
        raise DomainError('contact', '接触状态须为 ok 或 open；open 不得携带测值')
    if kind == 'repeat':
        identifier(parent)
    elif parent:
        raise DomainError('parent', '首测记录不能填写父批次')
    return {'x': read_number(x, -2048, 2048, '设备横坐标'),
            'y': read_number(y, -2048, 2048, '设备纵坐标'),
            'contact': contact, 'parent': parent,
            'leak_na': read_number(leak, 0, 1000000, '漏电') if contact == 'ok' else None,
            'margin_mv': read_number(margin, -10000, 10000, '裕量') if contact == 'ok' else None}


def parse(text, kind):
    if not isinstance(text, str) or len(text) > 240000:
        raise DomainError('csv_size', '探针表须为不超过 240000 字符的 CSV')
    try:
        rows = csv.reader(io.StringIO(text.removeprefix('\ufeff'), newline=''), strict=True)
        if next(rows, None) != HEADER:
            raise DomainError('csv_header', '表头须为 ' + ','.join(HEADER))
        result = []
        for index, row in enumerate(rows, 2):
            if index > 4097:
                raise DomainError('csv_row', f'第 {index} 条记录超出行数上限')
            try:
                result.append(read_row(row, kind))
            except DomainError as exc:
                raise DomainError(exc.code, f'第 {index} 条记录：{exc}') from exc
    except csv.Error as exc:
        raise DomainError('csv_syntax', 'CSV 引号或字段不合法') from exc
    if not result:
        raise DomainError('csv_empty', '批次没有数据行')
    return result


def scan(text):
    """逐行辨识复测来件：整表不可辨识时抛 DomainError，否则逐行给出记录或故障。"""
    if not isinstance(text, str) or len(text) > 240000:
        raise DomainError('csv_size', '探针表须为不超过 240000 字符的 CSV')
    try:
        rows = csv.reader(io.StringIO(text.removeprefix('\ufeff'), newline=''), strict=True)
        if next(rows, None) != HEADER:
            raise DomainError('csv_header', '表头须为 ' + ','.join(HEADER))
        result = []
        for index, row in enumerate(rows, 2):
            if index > 4097:
                raise DomainError('csv_row', '来件超出行数上限')
            try:
                result.append({'line': index, 'record': read_row(row, 'repeat')})
            except DomainError as exc:
                result.append({'line': index, 'fault': str(exc)})
    except csv.Error as exc:
        raise DomainError('csv_syntax', 'CSV 引号或字段不合法') from exc
    if not result:
        raise DomainError('csv_empty', '来件没有数据行')
    return result


def check_record(data):
    fields(data, 'x', 'y', 'contact', 'leak_na', 'margin_mv', 'parent')
    contact = data['contact']
    if contact not in ('ok', 'open') or (contact == 'open' and (data['leak_na'] is not None or data['margin_mv'] is not None)):
        raise DomainError('contact', '接触状态须为 ok 或 open；open 不得携带测值')
    return {'x': integer(data['x'], -2048, 2048, '设备横坐标'),
            'y': integer(data['y'], -2048, 2048, '设备纵坐标'),
            'contact': contact, 'parent': identifier(data['parent']),
            'leak_na': integer(data['leak_na'], 0, 1000000, '漏电') if contact == 'ok' else None,
            'margin_mv': integer(data['margin_mv'], -10000, 10000, '裕量') if contact == 'ok' else None}
