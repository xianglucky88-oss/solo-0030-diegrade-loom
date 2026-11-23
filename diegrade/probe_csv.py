import csv
import io
import re

from .errors import DomainError
from .validation import identifier, integer

HEADER = ['x', 'y', 'contact', 'leak_na', 'margin_mv', 'parent']


def read_number(text, low, high, label):
    if not re.fullmatch(r'-?[0-9]{1,8}', text):
        raise DomainError('csv_number', f'{label}格式错误')
    return integer(int(text), low, high, label)


def parse(text, kind):
    if not isinstance(text, str) or len(text) > 240000:
        raise DomainError('csv_size', '探针表须为不超过 240000 字符的 CSV')
    try:
        rows = csv.reader(io.StringIO(text.removeprefix('\ufeff'), newline=''), strict=True)
        if next(rows, None) != HEADER:
            raise DomainError('csv_header', '表头须为 ' + ','.join(HEADER))
        result = []
        for index, row in enumerate(rows, 2):
            if index > 4097 or len(row) != len(HEADER):
                raise DomainError('csv_row', f'第 {index} 条记录列数不正确或超出行数上限')
            x, y, contact, leak, margin, parent = row
            try:
                if contact not in ('ok', 'open') or (contact == 'open' and (leak or margin)):
                    raise DomainError('contact', '接触状态须为 ok 或 open；open 不得携带测值')
                if kind == 'repeat':
                    identifier(parent)
                elif parent:
                    raise DomainError('parent', '首测记录不能填写父批次')
                result.append({'x': read_number(x, -2048, 2048, '设备横坐标'),
                               'y': read_number(y, -2048, 2048, '设备纵坐标'),
                               'contact': contact, 'parent': parent,
                               'leak_na': read_number(leak, 0, 1000000, '漏电') if contact == 'ok' else None,
                               'margin_mv': read_number(margin, -10000, 10000, '裕量') if contact == 'ok' else None})
            except DomainError as exc:
                raise DomainError(exc.code, f'第 {index} 条记录：{exc}') from exc
    except csv.Error as exc:
        raise DomainError('csv_syntax', 'CSV 引号或字段不合法') from exc
    if not result:
        raise DomainError('csv_empty', '批次没有数据行')
    return result
