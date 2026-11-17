# -*- coding: utf-8 -*-
"""Read-only CalDAV discovery and recurrence parser, shared with the original local workbench. No stored credentials."""
import base64
import datetime
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DEFAULT_SERVER = "https://caldav.feishu.cn"

PAST_DAYS = 60       # 往前取多少天
FUTURE_DAYS = 220    # 往后取多少天（覆盖一学期）
MAX_EVENTS = 5000

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
DAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


# ---------------- 输出编码 ----------------
def setup_stdout():
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------- HTTP ----------------
def request(method, url, body=None, headers=None, auth=None, timeout=40, tries=3):
    """带重试的请求：网络抖动 / TLS 握手超时 / 5xx 会自动重试"""
    data = body.encode("utf-8") if isinstance(body, str) else body
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/xml; charset=utf-8")
        req.add_header("User-Agent", "wb-workbench-sync/1.0")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if auth:
            token = base64.b64encode(("%s:%s" % auth).encode("utf-8")).decode("ascii")
            req.add_header("Authorization", "Basic " + token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                last = e
                time.sleep(1.2 * (i + 1))
                continue
            raise
        except Exception as e:            # 超时 / 连接重置 / TLS 握手失败
            last = e
            if i < tries - 1:
                time.sleep(1.2 * (i + 1))
                continue
            raise
    if last:
        raise last
    raise RuntimeError("请求失败")


# ---------------- CalDAV 发现与查询 ----------------
PROPFIND_CALS = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:resourcetype/><d:displayname/></d:prop>
</d:propfind>"""

PROPFIND_PRINCIPAL = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop><d:current-user-principal/></d:prop>
</d:propfind>"""

# 注意：XML 命名空间前缀必须在根元素上声明（根元素用的前缀自己也要声明），
# 否则是非法 XML —— 飞书服务器会直接回 412 Precondition Failed。
REPORT_QUERY = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start}" end="{end}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

REPORT_QUERY_NORANGE = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT"/>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

# 飞书实测：calendar-query 只回 etag 不回正文；multiget 才能拿到事件正文
REPORT_MULTIGET = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-multiget xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:d="DAV:">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
{hrefs}
</c:calendar-multiget>"""

PROPFIND_LIST = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
</d:propfind>"""


def parse_multistatus_calendars(xml_text):
    root = ET.fromstring(xml_text)
    out = []
    for resp in root.findall("d:response", NS):
        href = resp.findtext("d:href", "", NS)
        if not href:
            continue
        prop = resp.find("d:propstat/d:prop", NS)
        if prop is None:
            continue
        rt = prop.find("d:resourcetype", NS)
        if rt is None or rt.find("c:calendar", NS) is None:
            continue
        name = prop.findtext("d:displayname", "", NS) or href.rstrip("/").rsplit("/", 1)[-1]
        out.append({"href": href, "name": name})
    return out


def discover_calendars(base, auth):
    status, text = request("PROPFIND", base, PROPFIND_CALS, {"Depth": "1"}, auth)
    cals = parse_multistatus_calendars(text)
    if cals:
        return cals
    # 兜底：先找 current-user-principal，再在它的 href 下找日历
    try:
        status, text = request("PROPFIND", base, PROPFIND_PRINCIPAL, {"Depth": "0"}, auth)
        root = ET.fromstring(text)
        href = root.findtext("d:response/d:propstat/d:prop/d:current-user-principal/d:href", "", NS)
        if href:
            status, text = request("PROPFIND", urllib.parse.urljoin(base, href), PROPFIND_CALS, {"Depth": "1"}, auth)
            return parse_multistatus_calendars(text)
    except Exception:
        pass
    return []


def extract_calendar_data(xml_text):
    """从 multistatus 响应里抽出所有含 VEVENT 的 calendar-data 文本"""
    root = ET.fromstring(xml_text)
    out = []
    for resp in root.findall("d:response", NS):
        cd = resp.find("d:propstat/d:prop/c:calendar-data", NS)
        if cd is not None and cd.text and "BEGIN:VEVENT" in cd.text:
            out.append(cd.text)
    return out


def extract_event_hrefs(xml_text):
    """从 multistatus 响应里抽出所有 .ics 事件文件的 href"""
    root = ET.fromstring(xml_text)
    return [h for h in (resp.findtext("d:href", "", NS) for resp in root.findall("d:response", NS))
            if h and h.endswith(".ics")]


def fetch_by_multiget(base, auth, cal_url, hrefs):
    """PROPFIND 列出事件文件后，用 calendar-multiget 分批拉取正文（飞书的实际可行通道）

    实测：飞书单次 multiget 40 条也接得住，但网络抖动常见，
    所以按 20 条一批、批间留间隙、单批失败不影响整体（能拿到多少用多少）。
    """
    out, errors = [], []
    for i in range(0, len(hrefs), 20):
        batch = hrefs[i:i + 20]
        body = REPORT_MULTIGET.format(
            hrefs="".join("<d:href>%s</d:href>" % h for h in batch))
        try:
            status, text = request("REPORT", cal_url, body, {"Depth": "1"}, auth)
            out.extend(extract_calendar_data(text))
        except Exception as e:
            errors.append(e)
        time.sleep(0.25)
    if not out and errors:
        raise errors[0]
    if errors:
        print("    （有 %d 批没拉成功，本次先用其余 %d 条）" % (len(errors), len(out)))
    return out


def fetch_ics_texts(base, auth, cal_href, start_utc=None, end_utc=None):
    """多级回退拉取日历事件：返回 (ics 文本列表, 实际采用的通道名)

    通道 1：REPORT calendar-query + time-range（标准服务器）
    通道 2：REPORT calendar-query 不带时间范围
    通道 3：PROPFIND 列出事件文件 + multiget 批量拉正文（飞书实测走这条）
    通道 4：GET 整个日历（少数服务器支持）
    """
    url = urllib.parse.urljoin(base, cal_href)
    attempts = []
    if start_utc and end_utc:
        attempts.append(("REPORT 时间范围", "REPORT",
                         REPORT_QUERY.format(start=start_utc, end=end_utc), {"Depth": "1"}))
    attempts.append(("REPORT 全量", "REPORT", REPORT_QUERY_NORANGE, {"Depth": "1"}))
    attempts.append(("PROPFIND 列表", "PROPFIND", PROPFIND_LIST, {"Depth": "1"}))

    last_err = None
    for name, method, body, headers in attempts:
        try:
            status, text = request(method, url, body, headers, auth)
            ics_list = extract_calendar_data(text)
            if ics_list:
                return ics_list, name
            # 拿到了事件文件列表但没有正文（飞书行为）→ multiget 补拉正文
            hrefs = extract_event_hrefs(text)
            if hrefs and method in ("REPORT", "PROPFIND"):
                got = fetch_by_multiget(base, auth, url, hrefs)
                if got:
                    return got, "multiget"
            if method == "PROPFIND" and status == 207:
                # PROPFIND 本身成功但没有 .ics 文件 → 空日历，不算失败
                return [], "空日历"
        except urllib.error.HTTPError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    # 最后试一下 GET 整个日历
    try:
        status, text = request("GET", url, None, {"Accept": "text/calendar"}, auth)
        if "BEGIN:VEVENT" in text:
            return [text], "GET"
    except Exception as e:
        last_err = e

    if last_err is not None:
        raise last_err
    raise RuntimeError("服务器没有返回任何事件")


# ---------------- ICS 解析 ----------------
def unfold_lines(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line.rstrip("\n"))
    return [l for l in out if l]


def split_prop(line):
    i = line.find(":")
    if i < 0:
        return None
    left, value = line[:i], line[i + 1:]
    parts = left.split(";")
    name = parts[0].strip().upper()
    params = {}
    for p in parts[1:]:
        j = p.find("=")
        if j > 0:
            params[p[:j].strip().upper()] = p[j + 1:].strip().strip('"')
    return name, params, value


def parse_vevents(ics_text):
    events, cur = [], None
    for line in unfold_lines(ics_text):
        if line.upper() == "BEGIN:VEVENT":
            cur = []
        elif line.upper() == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None:
            prop = split_prop(line)
            if prop:
                cur.append(prop)
    return events


def unescape_text(v):
    out = v.replace("\\\\", "\x00").replace("\\n", "\n").replace("\\N", "\n")
    out = out.replace("\\,", ",").replace("\\;", ";").replace("\x00", "\\")
    return out


def parse_dt(value, params=None):
    """返回 (naive_local_datetime, is_all_day)"""
    params = params or {}
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v):
        return datetime.datetime.strptime(v[:8], "%Y%m%d"), True
    if v.endswith("Z"):
        dt = datetime.datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone().replace(tzinfo=None), False
    dt = datetime.datetime.strptime(v, "%Y%m%dT%H%M%S")
    return dt, False


def parse_duration(v):
    m = re.fullmatch(
        r"([+-]?)P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    weeks, days, hours, minutes, seconds = (int(x) if x else 0 for x in m.groups()[1:])
    return sign * datetime.timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_rrule(value):
    out = {"freq": None, "interval": 1, "byday": None, "until": None, "count": None}
    for part in value.split(";"):
        k, _, v = part.partition("=")
        k = k.strip().upper()
        if not k:
            continue
        if k == "FREQ":
            out["freq"] = v.strip().upper()
        elif k == "INTERVAL":
            try:
                out["interval"] = max(1, int(v))
            except ValueError:
                pass
        elif k == "BYDAY":
            out["byday"] = [x.strip().upper()[-2:] for x in v.split(",") if x.strip()]
        elif k == "COUNT":
            try:
                out["count"] = int(v)
            except ValueError:
                pass
        elif k == "UNTIL":
            out["until"] = v.strip()
    return out


def parse_until(v):
    if not v:
        return None
    if re.fullmatch(r"\d{8}", v):
        return datetime.datetime.strptime(v, "%Y%m%d") + datetime.timedelta(days=1, seconds=-1)
    dt, _ = parse_dt(v)
    return dt


def week_start(dt):
    return dt - datetime.timedelta(days=dt.weekday())


def month_diff(a, b):
    return (a.year - b.year) * 12 + (a.month - b.month)


# ---------------- 事件构建 ----------------
def times_of(d):
    """(start, duration, all_day)"""
    params, value = d["DTSTART"]
    start, allday = parse_dt(value, params)
    if "DTEND" in d:
        p2, v2 = d["DTEND"]
        end, _ = parse_dt(v2, p2)
        dur = end - start
        if allday and dur <= datetime.timedelta(0):
            dur = datetime.timedelta(days=1)
        if dur < datetime.timedelta(0):
            dur = datetime.timedelta(hours=1)
    elif "DURATION" in d:
        dur = parse_duration(d["DURATION"][1]) or datetime.timedelta(hours=1)
    else:
        dur = datetime.timedelta(days=1) if allday else datetime.timedelta(hours=1)
    return start, dur, allday


def expand_master(d, win_start, win_end):
    """展开周期事件，yield (start, duration, all_day)"""
    start, dur, allday = times_of(d)
    if "RRULE" not in d:
        if win_start <= start <= win_end:
            yield start, dur, allday
        return

    rr = parse_rrule(d["RRULE"][1])
    until = parse_until(rr["until"])
    limit = min(until, win_end) if until else win_end
    base_day = datetime.datetime.combine(start.date(), datetime.time())
    cur = base_day
    count = 0
    guard = 0
    while cur <= limit and guard < 6000:
        guard += 1
        if rr["freq"] == "DAILY":
            hit = (cur - base_day).days % rr["interval"] == 0
        elif rr["freq"] == "WEEKLY":
            code = DAY_CODES[cur.weekday()]
            same = (code in rr["byday"]) if rr["byday"] else (cur.weekday() == start.weekday())
            wk = (week_start(cur) - week_start(base_day)).days // 7
            hit = same and wk % rr["interval"] == 0
        elif rr["freq"] == "MONTHLY":
            hit = cur.day == start.day and month_diff(cur, base_day) % rr["interval"] == 0
        else:
            hit = cur == base_day
        if hit:
            occ = cur.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            count += 1
            if win_start <= occ <= win_end:
                yield occ, dur, allday
            if rr["count"] and count >= rr["count"]:
                return
        cur += datetime.timedelta(days=1)


def make_event(uid, start, dur, allday, d, cal_name, master_uid=None):
    end = start + dur

    def txt(name):
        return unescape_text(d[name][1]) if name in d else ""

    return {
        "id": "%s@%s" % (uid, start.strftime("%Y%m%dT%H%M%S")),
        # uid：稳定的系列标识，工作台的「手动修正」按它锚定，重新同步不会丢
        "uid": master_uid or uid,
        "title": txt("SUMMARY") or "(无标题)",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "allDay": allday,
        "location": txt("LOCATION"),
        "description": txt("DESCRIPTION"),
        "calendar": cal_name,
    }


def build_events(cal_name, ics_text, win_start, win_end):
    masters, overrides, cancelled, exdates_of = {}, {}, set(), {}
    for props in parse_vevents(ics_text):
        d = {}
        exdates = set()
        for name, params, value in props:
            if name == "EXDATE":
                for v in value.split(","):
                    if v.strip():
                        try:
                            dt, _ = parse_dt(v.strip(), params)
                            exdates.add(dt)
                        except ValueError:
                            pass
            elif name not in d:
                d[name] = (params, value)
        uid = (d.get("UID", ({}, ""))[1] or "").strip() or ("noid-%d" % len(masters))
        if (d.get("STATUS", ({}, ""))[1] or "").strip().upper() == "CANCELLED":
            if "RECURRENCE-ID" in d:
                p, v = d["RECURRENCE-ID"]
                rid, _ = parse_dt(v, p)
                cancelled.add((uid, rid))
            continue
        if "DTSTART" not in d:
            continue
        if "RECURRENCE-ID" in d:
            p, v = d["RECURRENCE-ID"]
            rid, _ = parse_dt(v, p)
            overrides[(uid, rid)] = d
        else:
            masters[uid] = d
            exdates_of[uid] = exdates

    out = []
    for uid, d in masters.items():
        exdates = exdates_of.get(uid, set())
        for occ, dur, allday in expand_master(d, win_start, win_end):
            if (uid, occ) in cancelled or (uid, occ) in overrides:
                continue
            if occ in exdates:
                continue
            if allday and any(occ.date() == x.date() for x in exdates):
                continue
            out.append(make_event(uid, occ, dur, allday, d, cal_name))

    for (uid, rid), d in overrides.items():
        start, dur, allday = times_of(d)
        if win_start <= start <= win_end:
            out.append(make_event(uid + "-ov" + rid.strftime("%Y%m%dT%H%M%S"), start, dur, allday, d, cal_name, master_uid=uid))
    return out


