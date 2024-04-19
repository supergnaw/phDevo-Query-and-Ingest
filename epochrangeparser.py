import datetime
import re
import time


def is_increment(time_str: str) -> bool:
    return True if re.fullmatch(re.compile(r'^\d+[ybwdhms]$', re.IGNORECASE), time_str) else False


def is_timestamp(time_str: str) -> bool:
    return True if re.fullmatch(r'^\d{4}-\d{2}-\d{2}(\s\d{2}:\d{2}(:\d{2})?)?$', time_str) else False


def parse_timestamp(time_str: str) -> float:
    if re.fullmatch(r'^\d{4}-\d\d-\d\d\s\d\d:\d\d:\d\d$', time_str):
        return datetime.datetime.now().strptime(time_str, "%Y-%m-%d %H:%M:%S").timestamp()

    if re.fullmatch(r'^\d{4}-\d\d-\d\d\s\d\d:\d\d$', time_str):
        return datetime.datetime.now().strptime(time_str, "%Y-%m-%d %H:%M").timestamp()

    if re.fullmatch(r'^\d{4}-\d\d-\d\d$', time_str):
        return datetime.datetime.now().strptime(time_str, "%Y-%m-%d").timestamp()

    return time.time()


def parse_increment(time_str: str) -> float:
    time_offset = 0.0

    if re.fullmatch(r'^\d+[ybwdhms]$', time_str):

        if 'y' == time_str[-1]:  # year
            time_offset = time_offset + (int(time_str[0:-1]) * 31536000)

        if 'b' == time_str[-1]:  # month
            time_offset = time_offset + (int(time_str[0:-1]) * 2678400)

        if 'w' == time_str[-1]:  # week
            time_offset = time_offset + (int(time_str[0:-1]) * 604800)

        if 'd' == time_str[-1]:  # day
            time_offset = time_offset + (int(time_str[0:-1]) * 86400)

        if 'h' == time_str[-1]:  # hour
            time_offset = time_offset + (int(time_str[0:-1]) * 3600)

        if 'm' == time_str[-1]:  # minute
            time_offset = time_offset + (int(time_str[0:-1]) * 60)

        if 's' == time_str[-1]:  # second
            time_offset = time_offset + (int(time_str[0:-1]) * 1)

    return time_offset


def parse_range(time_str: str, default_range: str = "2w") -> tuple:
    time_str = time_str.lower().strip()
    default_range = default_range.lower().strip()

    time_beg = None
    time_end = None

    matches = re.findall(r'((\d{4}-\d{2}-\d{2}(\s\d{2}:\d{2}(:\d{2})?)?)|\d+[smhdwby]|now)', time_str)

    if 1 > len(matches) or 2 < len(matches):
        return time_beg, time_end

    if 2 > len(matches):
        matches.append(("now",) if "now" not in matches[0] else default_range)

    time_str_a = matches[0][0]
    time_str_b = matches[1][0]

    if is_timestamp(time_str_a) and is_timestamp(time_str_b):
        time_beg = min(parse_timestamp(time_str_a), parse_timestamp(time_str_b))
        time_end = max(parse_timestamp(time_str_a), parse_timestamp(time_str_b))

    if is_increment(time_str_a) and is_increment(time_str_b):
        time_beg = time.time() - min(parse_timestamp(time_str_a), parse_timestamp(time_str_b))
        time_end = time.time() - max(parse_timestamp(time_str_a), parse_timestamp(time_str_b))

    if "now" in [time_str_a, time_str_b]:
        time_end = time.time()
        beginning = time_str_a if "now" != time_str_a else time_str_b

        if is_timestamp(beginning):
            time_beg = parse_timestamp(beginning)
        if is_increment(beginning):
            time_beg = parse_increment(beginning)

    offset = time_str.replace(time_str_a, "").replace(time_str_b, "").strip()
    offset = "-" if 1 != len(offset) else offset

    if is_timestamp(time_str_a) and is_increment(time_str_b):
        if "+" == offset:
            time_beg = parse_timestamp(time_str_a)
            time_end = time_beg + parse_increment(time_str_b)
        if "-" == offset:
            time_end = parse_timestamp(time_str_a)
            time_beg = time_end - parse_increment(time_str_b)

    if is_increment(time_str_a) and is_timestamp(time_str_b):
        if "+" == offset:
            time_beg = parse_timestamp(time_str_b)
            time_end = time_beg + parse_increment(time_str_a)
        if "-" == offset:
            time_end = parse_timestamp(time_str_b)
            time_beg = time_end - parse_increment(time_str_a)

    return time_beg, time_end
