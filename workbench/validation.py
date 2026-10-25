"""User-facing parsing shared by routes and database pagination."""
import re


class InputError(ValueError):
    pass


def page_number(value):
    raw = str(value or '1').strip()
    if not re.fullmatch(r'[+-]?\d+', raw):
        raise InputError('页码请填写整数，例如 1；可返回岗位列表继续操作。')
    try:
        page = max(1, int(raw))
    except ValueError:
        raise InputError('页码过长，请填写有效页码。') from None
    if page > 9223372036854775807 // 50:
        raise InputError('页码过大，请填写有效页码。')
    return page


def optional_integer(value, label):
    raw = (value or '').strip()
    if not raw:
        return None
    if not re.fullmatch(r'[+-]?\d+', raw):
        raise InputError(f'{label}请填写完整整数，例如 1000；不支持小数、逗号、单位或指数写法。')
    try:
        number = int(raw)
    except ValueError:
        raise InputError(f'{label}过长，请检查后重试。') from None
    if number < 0:
        raise InputError(f'{label}不能为负数。')
    if number > 9223372036854775807:
        raise InputError(f'{label}过大，请检查后重试。')
    return number
