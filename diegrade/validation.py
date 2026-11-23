import re

from .errors import DomainError


def fields(value, *names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise DomainError('fields', '请求字段应恰为：' + '、'.join(names))
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}', value):
        raise DomainError('identifier', '编号须为 1–40 个字母、数字、连字符或下划线，并以字母或数字开头')
    return value


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise DomainError('number', f'{label}须为 {low}–{high} 间的整数')
    return value


def reason(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 240:
        raise DomainError('reason', '复核说明须为 1–240 字')
    return value.strip()
