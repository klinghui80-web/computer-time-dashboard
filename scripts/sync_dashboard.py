from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(r"C:\Users\Klh\Documents\computer-time-dashboard")
DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"
DOCS_DIR = ROOT / "docs"
HISTORY_PATH = DATA_DIR / "history.json"
STATE_PATH = DATA_DIR / "state.json"
INDEX_PATH = SITE_DIR / "index.html"
NOJEKYLL_PATH = SITE_DIR / ".nojekyll"
DOCS_INDEX_PATH = DOCS_DIR / "index.html"
DOCS_NOJEKYLL_PATH = DOCS_DIR / ".nojekyll"
DB_PATH = Path(r"C:\Users\Klh\AppData\Local\activitywatch\activitywatch\aw-server\peewee-sqlite.v2.db")
LOCAL_TZ = datetime.now().astimezone().tzinfo
DELIVERY_TIME = time(hour=19, minute=30)
WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
CATEGORY_COLORS = {
    "工作/创作": "#7aa2ff",
    "学习/信息": "#61ddaa",
    "沟通": "#5ad1e6",
    "设计": "#f6bd16",
    "娱乐": "#ff7d7d",
    "系统/维护": "#8a7dff",
    "生活/杂务": "#ffa940",
    "其他": "#cbd5e1",
}
CATEGORY_RULES = [
    ("工作/创作", [
        "hermes", "terminal", "powershell", "cursor", "vscode", "code", "github", "gitlab",
        "pycharm", "notepad++", "windsurf", "openai", "claude", "chatgpt", "jupyter",
        "python", "excel", "word", "powerpoint", "docs", "sheets", "obsidian", "notion",
    ]),
    ("设计", ["figma", "photoshop", "illustrator", "canva", "sketch", "after effects", "premiere"]),
    ("沟通", ["微信", "wechat", "feishu", "lark", "钉钉", "qq", "slack", "discord", "telegram", "teams", "outlook", "mail"]),
    ("娱乐", ["bilibili", "哔哩", "douyin", "抖音", "youtube", "netflix", "spotify", "music", "steam", "media player", "播放器"]),
    ("学习/信息", ["wikipedia", "arxiv", "read", "docs", "blog", "news", "google chrome", "edge", "browser", "浏览器", "activitywatch", "search", "搜索"]),
    ("系统/维护", ["explorer", "setting", "设置", "control panel", "task manager", "clash", "installer", "install", "下载", "驱动", "program manager", "quick settings", "shellhost", "文件资源管理器"]),
    ("生活/杂务", ["taobao", "京东", "美团", "外卖", "calendar", "todo", "地图", "map", "shop", "shopping", "支付宝", "bank"]),
]
BROWSER_APPS = {"chrome.exe", "msedge.exe", "firefox.exe", "browser", "chrome", "edge"}


@dataclass
class Segment:
    start: datetime
    end: datetime
    seconds: float
    app: str
    title: str
    activity: str
    category: str


def ensure_dirs() -> None:
    for path in (DATA_DIR, SITE_DIR, DOCS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now")
    parser.add_argument("--force-date")
    parser.add_argument("--mark-sent-date")
    parser.add_argument("--rebuild-days", type=int, default=14)
    parser.add_argument("--send-all-missed", action="store_true")
    parser.add_argument("--skip-push", action="store_true")
    parser.add_argument("--print-dry-run", action="store_true")
    return parser.parse_args()


def local_now(args: argparse.Namespace) -> datetime:
    if args.now:
        return datetime.fromisoformat(args.now).astimezone(LOCAL_TZ)
    return datetime.now().astimezone()


def dt_to_local(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(LOCAL_TZ)


def start_of_day(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=LOCAL_TZ)


def end_of_day(day: date) -> datetime:
    return start_of_day(day) + timedelta(days=1)


def fmt_duration(seconds: float) -> str:
    total = int(round(max(seconds, 0)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}小时")
    if m:
        parts.append(f"{m}分")
    if not parts:
        parts.append(f"{s}秒")
    return "".join(parts)


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def slug_week(start_day: date, end_day: date) -> str:
    return f"{start_day.isoformat()}~{end_day.isoformat()}"


def parse_datastr(text: str) -> dict[str, Any]:
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}


def open_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"ActivityWatch 数据库不存在: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def latest_bucket_key(conn: sqlite3.Connection, bucket_type: str) -> int | None:
    row = conn.execute(
        "SELECT key FROM bucketmodel WHERE type = ? ORDER BY created DESC LIMIT 1",
        (bucket_type,),
    ).fetchone()
    return int(row[0]) if row else None


def fetch_events(conn: sqlite3.Connection, bucket_key: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT timestamp, duration, datastr FROM eventmodel WHERE bucket_id = ? AND timestamp < ? ORDER BY timestamp ASC",
        (bucket_key, end.astimezone().isoformat(sep=" ")),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        s = dt_to_local(row[0])
        duration = float(row[1] or 0)
        e = s + timedelta(seconds=duration)
        if e <= start or s >= end or duration <= 0:
            continue
        events.append({"start": s, "end": e, "seconds": duration, "data": parse_datastr(row[2])})
    return events


def intersect_ranges(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> tuple[datetime, datetime] | None:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end <= start:
        return None
    return start, end


def load_active_ranges(conn: sqlite3.Connection, day: date) -> list[tuple[datetime, datetime]]:
    bucket_key = latest_bucket_key(conn, "afkstatus")
    if bucket_key is None:
        return []
    ranges = []
    for event in fetch_events(conn, bucket_key, start_of_day(day), end_of_day(day)):
        if event["data"].get("status") == "not-afk":
            ranges.append((event["start"], event["end"]))
    return ranges


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def enrich_browser_activity(app: str, title: str, start: datetime, web_events: list[dict[str, Any]]) -> tuple[str, str]:
    app_name = (app or "").lower()
    if app_name not in BROWSER_APPS and app_name.replace(".exe", "") not in BROWSER_APPS:
        return title, title
    for event in web_events:
        overlap = intersect_ranges(start, start + timedelta(seconds=1), event["start"], event["end"])
        if overlap or abs((event["start"] - start).total_seconds()) <= 8:
            url = event["data"].get("url", "")
            page_title = normalize_text(event["data"].get("title") or title)
            domain = urlparse(url).netloc
            if page_title and domain:
                return f"{page_title}｜{domain}", page_title
            if page_title:
                return page_title, page_title
    return title, title


def classify_activity(activity: str, app: str, title: str) -> str:
    haystack = f"{activity} {app} {title}".lower()
    for category, keywords in CATEGORY_RULES:
        if any(keyword in haystack for keyword in keywords):
            return category
    return "其他"


def load_segments(conn: sqlite3.Connection, day: date) -> list[Segment]:
    bucket_key = latest_bucket_key(conn, "currentwindow")
    web_key = latest_bucket_key(conn, "web.tab.current")
    if bucket_key is None:
        return []
    active_ranges = load_active_ranges(conn, day)
    if not active_ranges:
        return []
    web_events = fetch_events(conn, web_key, start_of_day(day), end_of_day(day)) if web_key else []
    segments: list[Segment] = []
    for event in fetch_events(conn, bucket_key, start_of_day(day), end_of_day(day)):
        app = normalize_text(event["data"].get("app", "未知应用"))
        title = normalize_text(event["data"].get("title", "未命名窗口"))
        if not title:
            title = app
        enriched_activity, title_hint = enrich_browser_activity(app, title, event["start"], web_events)
        activity = f"{app}｜{enriched_activity}" if enriched_activity and app not in enriched_activity else enriched_activity or f"{app}｜{title}"
        category = classify_activity(activity, app, title_hint)
        for active_start, active_end in active_ranges:
            overlap = intersect_ranges(event["start"], event["end"], active_start, active_end)
            if not overlap:
                continue
            seg_start, seg_end = overlap
            seconds = (seg_end - seg_start).total_seconds()
            if seconds < 1:
                continue
            segments.append(Segment(seg_start, seg_end, seconds, app, title, activity, category))
    segments.sort(key=lambda item: item.start)
    return segments


def merge_focus_segments(segments: list[Segment]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for seg in segments:
        if merged:
            prev = merged[-1]
            gap = (seg.start - prev["end"]).total_seconds()
            if seg.activity == prev["activity"] and gap <= 90:
                prev["end"] = max(prev["end"], seg.end)
                prev["seconds"] += seg.seconds
                continue
        merged.append({
            "activity": seg.activity,
            "category": seg.category,
            "start": seg.start,
            "end": seg.end,
            "seconds": seg.seconds,
        })
    return merged


def build_daily_summary(day: date, segments: list[Segment]) -> dict[str, Any]:
    total = sum(seg.seconds for seg in segments)
    category_seconds: dict[str, float] = defaultdict(float)
    activity_seconds: dict[str, float] = defaultdict(float)
    category_reps: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    hourly_seconds: dict[str, float] = {str(hour): 0.0 for hour in range(24)}
    for seg in segments:
        category_seconds[seg.category] += seg.seconds
        activity_seconds[seg.activity] += seg.seconds
        category_reps[seg.category][seg.activity] += seg.seconds
        cursor = seg.start
        while cursor < seg.end:
            next_hour = (cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            chunk_end = min(next_hour, seg.end)
            hourly_seconds[str(cursor.hour)] += (chunk_end - cursor).total_seconds()
            cursor = chunk_end

    merged = merge_focus_segments(segments)
    longest = max(merged, key=lambda item: item["seconds"], default=None)
    switch_count = 0
    prev_activity = None
    for block in merged:
        if prev_activity and block["activity"] != prev_activity:
            switch_count += 1
        prev_activity = block["activity"]

    categories = []
    for name, seconds in sorted(category_seconds.items(), key=lambda item: item[1], reverse=True):
        reps = sorted(category_reps[name].items(), key=lambda item: item[1], reverse=True)[:3]
        categories.append({
            "name": name,
            "seconds": round(seconds, 2),
            "share": round(seconds / total, 4) if total else 0,
            "representatives": [rep for rep, _ in reps],
            "color": CATEGORY_COLORS.get(name, "#cbd5e1"),
        })

    top_categories = [item["name"] for item in categories[:3]]
    comm_share = next((item["share"] for item in categories if item["name"] == "沟通"), 0)
    entertainment_share = next((item["share"] for item in categories if item["name"] == "娱乐"), 0)
    sys_share = next((item["share"] for item in categories if item["name"] == "系统/维护"), 0)
    most_fragmented = max(categories, key=lambda item: item["share"] if item["name"] not in (top_categories[:1] or [""]) else 0, default=None)

    if total >= 4 * 3600 and switch_count <= 90:
        confidence = "高"
    elif total >= 90 * 60:
        confidence = "中"
    else:
        confidence = "低"

    evidence = [
        f"总活跃时长 {fmt_duration(total)}",
        f"窗口切换 {switch_count} 次",
        f"最长连续专注块 {fmt_duration(longest['seconds']) if longest else '0秒'}",
    ]
    summary_line = summarize_day_line(total, categories, switch_count)
    notes = build_daily_notes(total, categories, switch_count, longest)

    return {
        "date": day.isoformat(),
        "weekday": WEEKDAY_NAMES[day.weekday()],
        "total_active_seconds": round(total, 2),
        "total_active_text": fmt_duration(total),
        "top_categories": top_categories,
        "categories": categories,
        "switch_count": switch_count,
        "segment_count": len(segments),
        "summary_line": summary_line,
        "notes": notes,
        "longest_focus": {
            "category": longest["category"] if longest else "无",
            "activity": longest["activity"] if longest else "无",
            "seconds": round(longest["seconds"], 2) if longest else 0,
            "text": fmt_duration(longest["seconds"]) if longest else "0秒",
            "range": f"{longest['start'].strftime('%H:%M')}–{longest['end'].strftime('%H:%M')}" if longest else "—",
        },
        "energy": {
            "high_zone": top_categories[0] if top_categories else "无",
            "fragmented_zone": most_fragmented["name"] if most_fragmented else "无",
            "recovery_zone": "娱乐" if entertainment_share > 0.08 else ("沟通" if comm_share > 0.12 else ("系统/维护" if sys_share > 0.15 else "无明显恢复区")),
            "confidence": confidence,
            "evidence": evidence,
        },
        "hourly_seconds": {hour: round(value, 2) for hour, value in hourly_seconds.items()},
        "top_activities": [
            {"activity": activity, "seconds": round(seconds, 2), "text": fmt_duration(seconds)}
            for activity, seconds in sorted(activity_seconds.items(), key=lambda item: item[1], reverse=True)[:8]
        ],
        "generated_at": datetime.now().astimezone().isoformat(),
    }


def summarize_day_line(total: float, categories: list[dict[str, Any]], switch_count: int) -> str:
    if total == 0:
        return "这一天没有读到有效的电脑活跃记录。"
    lead = categories[0]["name"] if categories else "其他"
    if switch_count >= 120:
        rhythm = "切换密度偏高，像是在多线程推进。"
    elif switch_count >= 60:
        rhythm = "节奏中等，主线明确但中间有若干打断。"
    else:
        rhythm = "节奏相对稳定，存在可识别的连续工作块。"
    return f"这一天共记录 {fmt_duration(total)} 的活跃电脑时间，主轴是“{lead}”，{rhythm}"


def build_daily_notes(total: float, categories: list[dict[str, Any]], switch_count: int, longest: dict[str, Any] | None) -> list[str]:
    notes: list[str] = []
    if not categories:
        return ["今天没有足够的数据生成复盘提示。"]
    lead = categories[0]
    notes.append(f"主类目是“{lead['name']}”，占比 {pct(lead['share'])}，说明当天最主要的电脑注意力投向比较清晰。")
    if switch_count >= 120:
        notes.append("窗口切换次数较高，建议回看是否有过多的检索/切页/消息打断。")
    elif switch_count <= 40:
        notes.append("窗口切换不高，说明这一天的任务切换成本相对可控。")
    if longest and longest["seconds"] >= 45 * 60:
        notes.append(f"出现了 {fmt_duration(longest['seconds'])} 的长专注块，可以回看这段前后的环境条件并尝试复用。")
    elif longest and longest["seconds"] <= 10 * 60:
        notes.append("最长连续专注块偏短，后续可重点观察是什么因素把整块时间切碎了。")
    return notes[:3]


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_week_summary(end_day: date, days: list[dict[str, Any]]) -> dict[str, Any]:
    start_day = monday_of(end_day)
    selected = [item for item in days if start_day.isoformat() <= item["date"] <= end_day.isoformat()]
    total = sum(item.get("total_active_seconds", 0) for item in selected)
    by_category: dict[str, float] = defaultdict(float)
    daily_totals = []
    for item in selected:
        daily_totals.append({"date": item["date"], "seconds": item.get("total_active_seconds", 0), "text": item.get("total_active_text", "0秒")})
        for cat in item.get("categories", []):
            by_category[cat["name"]] += cat["seconds"]
    categories = [
        {
            "name": name,
            "seconds": round(seconds, 2),
            "share": round(seconds / total, 4) if total else 0,
            "color": CATEGORY_COLORS.get(name, "#cbd5e1"),
        }
        for name, seconds in sorted(by_category.items(), key=lambda item: item[1], reverse=True)
    ]
    top_days = sorted(daily_totals, key=lambda item: item["seconds"], reverse=True)[:3]
    summary = f"本周累计 {fmt_duration(total)}，主轴是“{categories[0]['name']}”。" if categories else f"本周累计 {fmt_duration(total)}。"
    notes = []
    if top_days:
        notes.append(f"最投入的一天是 {top_days[0]['date']}，活跃 {fmt_duration(top_days[0]['seconds'])}。")
    if len(selected) >= 2:
        weekend = sum(item["seconds"] for item in daily_totals if date.fromisoformat(item["date"]).weekday() >= 5)
        weekday = sum(item["seconds"] for item in daily_totals if date.fromisoformat(item["date"]).weekday() < 5)
        if weekend and weekday:
            notes.append(f"周末占比 {pct(weekend / (weekend + weekday))}，可用来观察休息日是否也被工作/信息流挤占。")
    if categories:
        notes.append(f"前三类目合计占比 {pct(sum(item['share'] for item in categories[:3]))}，说明这一周的电脑使用主线集中度。")
    return {
        "id": slug_week(start_day, end_day),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "label": f"{start_day.strftime('%m/%d')} - {end_day.strftime('%m/%d')}",
        "total_active_seconds": round(total, 2),
        "total_active_text": fmt_duration(total),
        "categories": categories,
        "daily_totals": daily_totals,
        "summary_line": summary,
        "notes": notes[:3],
        "generated_at": datetime.now().astimezone().isoformat(),
    }


def build_all_weeks(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date = {date.fromisoformat(item["date"]): item for item in days}
    sundays = sorted(day for day in by_date if day.weekday() == 6)
    return [build_week_summary(sunday, days) for sunday in sundays]


def update_history(now: datetime, rebuild_days: int, force_date: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    ensure_dirs()
    history = load_json(HISTORY_PATH, {"days": [], "weeks": [], "meta": {}})
    state = load_json(STATE_PATH, {"sent_days": [], "sent_weeks": [], "last_push": None})
    by_date = {item["date"]: item for item in history.get("days", [])}

    conn = open_db()
    try:
        if force_date:
            target_days = [date.fromisoformat(force_date)]
        else:
            target_days = [(now.date() - timedelta(days=offset)) for offset in range(rebuild_days + 1)]
        for day in target_days:
            summary = build_daily_summary(day, load_segments(conn, day))
            by_date[summary["date"]] = summary
    finally:
        conn.close()

    days = sorted(by_date.values(), key=lambda item: item["date"])
    weeks = build_all_weeks(days)
    history = {
        "days": days,
        "weeks": weeks,
        "meta": {
            "db_path": str(DB_PATH),
            "generated_at": now.isoformat(),
            "delivery_time": DELIVERY_TIME.strftime("%H:%M"),
            "timezone": str(now.tzinfo),
        },
    }
    save_json(HISTORY_PATH, history)
    return history, state


def eligible_unsent_day(history: dict[str, Any], state: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    sent = set(state.get("sent_days", []))
    forced = None
    for day in history.get("days", []):
        day_date = date.fromisoformat(day["date"])
        due = datetime.combine(day_date, DELIVERY_TIME, tzinfo=LOCAL_TZ)
        if day_date < now.date() or now >= due:
            if day["date"] not in sent and day.get("total_active_seconds", 0) > 0:
                forced = day
    return forced


def corresponding_week(history: dict[str, Any], day_record: dict[str, Any]) -> dict[str, Any] | None:
    target = date.fromisoformat(day_record["date"])
    if target.weekday() != 6:
        return None
    week_id = slug_week(monday_of(target), target)
    for week in history.get("weeks", []):
        if week.get("id") == week_id:
            return week
    return None


def mark_sent(state: dict[str, Any], day_record: dict[str, Any], week_record: dict[str, Any] | None) -> None:
    sent_days = set(state.get("sent_days", []))
    sent_days.add(day_record["date"])
    state["sent_days"] = sorted(sent_days)
    if week_record:
        sent_weeks = set(state.get("sent_weeks", []))
        sent_weeks.add(week_record["id"])
        state["sent_weeks"] = sorted(sent_weeks)
    save_json(STATE_PATH, state)


def mark_sent_by_date(history: dict[str, Any], state: dict[str, Any], target_date: str) -> None:
    day_record = next((item for item in history.get("days", []) if item.get("date") == target_date), None)
    if not day_record:
        raise ValueError(f"未找到要标记的日期: {target_date}")
    week_record = corresponding_week(history, day_record)
    mark_sent(state, day_record, week_record)


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_hidden(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)


def repo_has_remote() -> bool:
    result = run_hidden(["git", "remote", "get-url", "origin"], cwd=ROOT)
    return result.returncode == 0 and bool(result.stdout.strip())


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return run_hidden(args, cwd=ROOT)


def maybe_commit_and_push(now: datetime, skip_push: bool) -> dict[str, Any]:
    status = {"committed": False, "pushed": False, "message": "未配置远程仓库，已只在本地更新。"}
    run_git(["git", "add", "site/index.html", "site/.nojekyll", "docs/index.html", "docs/.nojekyll", "data/history.json", "data/state.json"])
    diff = run_git(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        status["message"] = "本次没有新的 Git 变更。"
        return status
    commit = run_git(["git", "commit", "-m", f"update dashboard {now.strftime('%Y-%m-%d %H:%M')}"])
    status["committed"] = commit.returncode == 0
    if not repo_has_remote() or skip_push:
        status["message"] = "本地仓库已更新，但尚未推送到 GitHub。"
        return status
    push = run_git(["git", "push", "origin", "main"])
    status["pushed"] = push.returncode == 0
    status["message"] = "已推送到 GitHub。" if push.returncode == 0 else f"GitHub 推送失败：{(push.stderr or push.stdout).strip()}"
    return status


def render_html(history: dict[str, Any]) -> str:
    template = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>个人工作台 · 时间、任务与复盘</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #07111f;
      --panel: rgba(13, 22, 42, 0.88);
      --panel-2: rgba(17, 28, 54, 0.9);
      --line: rgba(255,255,255,0.08);
      --text: #eff4ff;
      --muted: #9fb0d6;
    }
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--text); background: radial-gradient(circle at top, #12203e, var(--bg) 42%); font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif; }
    .shell { display: grid; grid-template-columns: 310px 1fr; min-height: 100vh; }
    .sidebar { padding: 24px 18px; border-right: 1px solid var(--line); background: rgba(6, 11, 24, 0.78); backdrop-filter: blur(20px); position: sticky; top: 0; height: 100vh; overflow: auto; }
    .brand h1 { margin: 0 0 8px; font-size: 24px; }
    .brand p { margin: 0; color: var(--muted); line-height: 1.7; font-size: 13px; }
    .meta-stack { display: grid; gap: 8px; margin-top: 16px; }
    .meta-pill { border: 1px solid var(--line); border-radius: 999px; padding: 8px 10px; font-size: 12px; color: var(--muted); background: rgba(255,255,255,0.03); }
    .tabs { display: flex; gap: 8px; margin: 18px 0 16px; }
    .tab { border: 1px solid var(--line); background: transparent; color: var(--text); border-radius: 999px; padding: 8px 12px; cursor: pointer; }
    .tab.active { background: rgba(122,162,255,0.16); border-color: rgba(122,162,255,0.45); }
    .list { display: none; gap: 10px; flex-direction: column; }
    .list.active { display: flex; }
    .item { width: 100%; text-align: left; background: var(--panel-2); border: 1px solid var(--line); border-radius: 16px; padding: 12px 14px; color: var(--text); cursor: pointer; }
    .item strong { display: block; font-size: 14px; }
    .item small { display: block; color: var(--muted); margin-top: 6px; line-height: 1.6; }
    .item.active { border-color: rgba(122,162,255,0.55); box-shadow: 0 0 0 1px rgba(122,162,255,0.2) inset; }
    .main { padding: 28px; }
    .header { display: flex; justify-content: space-between; gap: 16px; align-items: start; margin-bottom: 18px; }
    .header h2 { margin: 0 0 8px; font-size: 32px; }
    .header p { margin: 0; line-height: 1.7; color: var(--muted); max-width: 760px; }
    .header .info { color: var(--muted); font-size: 13px; text-align: right; }
    .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 16px; }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: 24px; padding: 20px; box-shadow: 0 18px 60px rgba(0,0,0,0.18); }
    .span-12 { grid-column: span 12; }
    .span-8 { grid-column: span 8; }
    .span-6 { grid-column: span 6; }
    .span-4 { grid-column: span 4; }
    .kpis { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }
    .kpi { padding: 16px; border-radius: 18px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06); }
    .kpi label { display:block; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
    .kpi strong { font-size: 24px; }
    .section-title { margin: 0 0 14px; font-size: 17px; }
    .bars { display:grid; gap:12px; }
    .bar-row { display:grid; grid-template-columns: 120px 1fr 100px; gap:10px; align-items:center; }
    .bar { height:10px; border-radius:999px; background: rgba(255,255,255,0.07); overflow:hidden; }
    .bar > span { display:block; height:100%; border-radius:999px; }
    .timeline { display:grid; grid-template-columns: repeat(24, minmax(0,1fr)); gap:6px; height:180px; align-items:end; }
    .hour { position:relative; min-height:6px; border-radius: 10px 10px 4px 4px; background: linear-gradient(180deg, rgba(122,162,255,0.95), rgba(122,162,255,0.18)); }
    .hour span { position:absolute; bottom:-22px; left:50%; transform:translateX(-50%); font-size:11px; color:var(--muted); }
    .chips { display:flex; flex-wrap:wrap; gap:8px; }
    .chip { padding:8px 10px; border-radius:999px; background: rgba(255,255,255,0.05); font-size: 13px; }
    table { width:100%; border-collapse:collapse; }
    th, td { text-align:left; padding:10px 0; border-bottom:1px solid rgba(255,255,255,0.08); vertical-align:top; }
    th { color:var(--muted); font-size:12px; font-weight:500; }
    .note-list { margin:0; padding-left:18px; line-height:1.8; }
    .subtle { color:var(--muted); }
    .workbench-hero { display:grid; grid-template-columns: 1.25fr .75fr; gap:16px; align-items:stretch; }
    .hero-title { font-size:42px; line-height:1.02; letter-spacing:-1.1px; margin:0 0 12px; font-weight:600; }
    .hero-copy { color:var(--muted); line-height:1.8; margin:0; max-width:780px; }
    .overview-first-fold { min-height: calc(100vh - 150px); display:flex; flex-direction:column; justify-content:center; gap:24px; background:linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.015)); }
    .overview-top { display:flex; flex-direction:column; align-items:center; gap:18px; text-align:center; }
    .overview-kicker { color:var(--muted); font-size:13px; letter-spacing:.08em; text-transform:uppercase; }
    .donut { width:300px; height:300px; border-radius:50%; display:grid; place-items:center; position:relative; filter:drop-shadow(0 24px 70px rgba(0,0,0,.25)); }
    .donut::after { content:""; position:absolute; inset:44px; border-radius:50%; background:rgba(7,17,31,.96); border:1px solid rgba(255,255,255,.08); }
    .donut-center { position:relative; z-index:1; display:grid; gap:6px; justify-items:center; }
    .donut-number { font-size:72px; line-height:.9; letter-spacing:-2px; font-weight:600; color:#f7f8f8; }
    .donut-label { color:#8a8f98; font-size:15px; }
    .status-legend { display:flex; justify-content:center; flex-wrap:wrap; gap:10px; }
    .legend-pill { display:inline-flex; align-items:center; gap:8px; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.035); color:#d0d6e0; border-radius:999px; padding:8px 13px; font-size:13px; }
    .dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
    .overview-metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }
    .metric-card { min-height:140px; border:1px solid rgba(255,255,255,.08); border-radius:22px; background:rgba(255,255,255,.028); padding:22px; display:flex; flex-direction:column; justify-content:space-between; }
    .metric-card label { color:#8a8f98; font-size:13px; }
    .metric-card strong { font-size:46px; line-height:1; letter-spacing:-1px; }
    .metric-card strong .unit { font-size:18px; color:#8a8f98; margin-left:4px; }
    .metric-card p { margin:0; color:#d0d6e0; line-height:1.55; }
    .next-fold { margin-top:4px; }
    .toolbar { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    .btn { border:1px solid rgba(255,255,255,0.08); color:var(--text); background:rgba(255,255,255,0.04); border-radius:10px; padding:9px 12px; cursor:pointer; font:inherit; }
    .btn.primary { background:#5e6ad2; border-color:#7170ff; color:#fff; }
    .btn.danger { color:#ffb4b4; border-color:rgba(255,125,125,.35); }
    .field, select, textarea, input:not([type="checkbox"]) { width:100%; color:var(--text); background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:10px 12px; font:inherit; outline:none; }
    input[type="checkbox"] { accent-color:#7170ff; width:16px; height:16px; margin-top:4px; }
    textarea { min-height:86px; resize:vertical; line-height:1.6; }
    .form-grid { display:grid; grid-template-columns: 1.2fr .55fr .55fr .7fr; gap:10px; align-items:end; }
    .form-grid .wide { grid-column: span 2; }
    .form-grid label, .review-grid label { display:grid; gap:6px; font-size:12px; color:var(--muted); }
    .task-list { display:grid; gap:10px; }
    .task-card { display:grid; grid-template-columns: auto 1fr auto; gap:12px; align-items:start; padding:14px; border:1px solid rgba(255,255,255,0.08); border-radius:16px; background:rgba(255,255,255,0.035); }
    .task-card.done { opacity:.58; }
    .task-main strong { display:block; margin-bottom:7px; }
    .task-main p { margin:0; color:var(--muted); line-height:1.6; font-size:13px; }
    .task-meta { display:flex; gap:6px; flex-wrap:wrap; margin-top:9px; }
    .badge { display:inline-flex; align-items:center; gap:5px; border-radius:999px; padding:5px 8px; font-size:11px; color:#d0d6e0; border:1px solid rgba(255,255,255,0.08); background:rgba(255,255,255,0.035); }
    .p-P0 { color:#ffb4b4; border-color:rgba(255,125,125,.35); }
    .p-P1 { color:#ffd59a; border-color:rgba(255,169,64,.35); }
    .p-P2 { color:#a9c3ff; border-color:rgba(122,162,255,.35); }
    .p-P3 { color:#b9c1d6; }
    .add-task-trigger { width:100%; margin-top:14px; padding:16px; border-radius:16px; border:1px dashed rgba(122,162,255,.45); background:rgba(122,162,255,.08); color:#dfe7ff; font-weight:600; cursor:pointer; }
    .task-modal-backdrop { position:fixed; inset:0; z-index:20; display:none; place-items:center; padding:24px; background:rgba(0,0,0,.62); backdrop-filter: blur(10px); }
    .task-modal-backdrop.open { display:grid; }
    .task-modal { width:min(760px, 100%); border:1px solid rgba(255,255,255,.12); border-radius:24px; background:#0f1728; box-shadow:0 24px 90px rgba(0,0,0,.45); padding:22px; }
    .task-modal-head { display:flex; align-items:start; justify-content:space-between; gap:14px; margin-bottom:16px; }
    .task-modal-head h3 { margin:0 0 6px; }
    .task-modal-head p { margin:0; color:var(--muted); line-height:1.6; }
    .icon-close { width:38px; height:38px; border-radius:999px; border:1px solid rgba(255,255,255,.1); background:rgba(255,255,255,.04); color:var(--text); cursor:pointer; }
    .review-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .review-grid .wide { grid-column:span 2; }
    .insight-list { display:grid; gap:10px; }
    .insight { padding:12px; border-radius:14px; background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.06); color:#d0d6e0; line-height:1.65; }
    .deadline-timeline { display:grid; gap:12px; }
    .deadline-row { display:grid; grid-template-columns:112px 1fr; gap:14px; align-items:start; }
    .deadline-date { color:#8a8f98; font-family:'JetBrains Mono', ui-monospace, monospace; font-size:12px; padding-top:3px; }
    .deadline-body { border-left:2px solid rgba(113,112,255,.45); padding-left:14px; color:#d0d6e0; line-height:1.65; }
    .deadline-body strong { display:block; color:#f7f8f8; margin-bottom:5px; }
    .view-switch { display:flex; gap:8px; margin-bottom:14px; }
    .view-switch button { border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.035); color:#d0d6e0; border-radius:999px; padding:8px 12px; cursor:pointer; }
    .view-switch button.active { color:#fff; border-color:rgba(122,162,255,.5); background:rgba(122,162,255,.16); }
    .deadline-calendar { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:8px; }
    .calendar-day { min-height:104px; border:1px solid rgba(255,255,255,.08); border-radius:14px; background:rgba(255,255,255,.025); padding:10px; }
    .calendar-day strong { display:block; font-size:12px; color:#8a8f98; margin-bottom:8px; }
    .calendar-task { display:block; margin-top:6px; padding:6px 8px; border-radius:10px; background:rgba(122,162,255,.12); color:#dfe7ff; font-size:12px; line-height:1.35; }
    .calendar-task .countdown { display:block; color:#8a8f98; margin-top:3px; }
    .empty { color:var(--muted); padding:18px; border:1px dashed rgba(255,255,255,0.14); border-radius:16px; text-align:center; }
    .mini-stat { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
    @media (max-width: 1100px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--line); }
      .span-8, .span-6, .span-4 { grid-column: span 12; }
      .kpis { grid-template-columns: repeat(2, minmax(0,1fr)); }
      .workbench-hero, .form-grid, .review-grid { grid-template-columns: 1fr; }
      .form-grid .wide { grid-column: span 1; }
      .review-grid .wide { grid-column: span 1; }
      .mini-stat { grid-template-columns:1fr; }
      .overview-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .donut { width:240px; height:240px; }
      .donut-number { font-size:58px; }
      .overview-first-fold { min-height:auto; }
      .deadline-calendar { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .task-modal { max-height:88vh; overflow:auto; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <h1>个人工作台</h1>
        <p>一个集成今日执行、待办优先级、手动复盘与时间管理的个人化工作台。原有每日 19:30 飞书复盘和时间统计自动化保持不动。</p>
      </div>
      <div class="meta-stack">
        <div class="meta-pill" id="generatedMeta"></div>
        <div class="meta-pill" id="sourceMeta"></div>
      </div>
      <div class="tabs">
        <button class="tab active" data-tab="workbench">工作台</button>
        <button class="tab" data-tab="days">时间与精力</button>
        <button class="tab" data-tab="weeks">每周</button>
      </div>
      <div class="list" id="dayList"></div>
      <div class="list" id="weekList"></div>
    </aside>
    <main class="main">
      <div class="header">
        <div>
          <h2 id="title">加载中…</h2>
          <p id="subtitle"></p>
        </div>
        <div class="info" id="meta"></div>
      </div>
      <section class="grid" id="content"></section>
    </main>
  </div>
  <script>
    const store = __STORE_JSON__;
    const days = [...store.days].filter(day => (day.total_active_seconds || 0) > 0).sort((a, b) => b.date.localeCompare(a.date));
    const weeks = [...store.weeks].filter(week => (week.total_active_seconds || 0) > 0).sort((a, b) => b.end_date.localeCompare(a.end_date));
    const tabs = document.querySelectorAll('.tab');
    const dayList = document.getElementById('dayList');
    const weekList = document.getElementById('weekList');
    const title = document.getElementById('title');
    const subtitle = document.getElementById('subtitle');
    const meta = document.getElementById('meta');
    const content = document.getElementById('content');
    const generatedMeta = document.getElementById('generatedMeta');
    const sourceMeta = document.getElementById('sourceMeta');

    generatedMeta.textContent = `最近一次生成：${new Date(store.meta.generated_at).toLocaleString('zh-CN')}`;
    sourceMeta.textContent = `数据源：ActivityWatch 本地库 · 发送时间：${store.meta.delivery_time}`;

    const fmtDuration = (seconds) => {
      seconds = Math.round(seconds || 0);
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = seconds % 60;
      if (h && m) return `${h}小时${m}分`;
      if (h) return `${h}小时${m ? `${m}分` : ''}`;
      if (m) return `${m}分`;
      return `${s}秒`;
    };
    const pct = (share) => `${(share * 100).toFixed(1)}%`;
    const today = days[0] || null;
    const WORKBENCH_TASK_KEY = 'workbenchTasks';
    const WORKBENCH_REVIEW_KEY = 'workbenchReviews';
    const priorityRank = { P0: 0, P1: 1, P2: 2, P3: 3 };
    const statusLabels = { todo: '待办', doing: '进行中', waiting: '等待中', blocked: '阻塞中', done: '已完成' };
    const statusStyles = {
      urgent: { label: '紧急', color: '#c95d45' },
      doing: { label: '进行中', color: '#4f6f92' },
      todo: { label: '待办', color: '#b89a45' },
      waiting: { label: '等待', color: '#4d9478' },
      done: { label: '已完成', color: '#63627f' },
    };

    const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const readLocal = (key, fallback) => {
      try { return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback)); }
      catch { return fallback; }
    };
    const writeLocal = (key, value) => localStorage.setItem(key, JSON.stringify(value));
    const uid = () => `task_${Date.now()}_${Math.random().toString(16).slice(2)}`;

    function loadTasks() {
      const saved = readLocal(WORKBENCH_TASK_KEY, null);
      if (saved && Array.isArray(saved)) return saved;
      const defaults = [
        { id: uid(), title: '确认个人工作台首页结构', description: '根据实际使用反馈调整今日总览、待办和复盘区块。', priority: 'P1', status: 'doing', project: '个人工作台', due: today?.date || '', focus: true, createdAt: new Date().toISOString() },
        { id: uid(), title: '晚上填写今日复盘', description: '记录完成事项、推进情况、阻塞和明日下一步。', priority: 'P0', status: 'todo', project: '每日复盘', due: today?.date || '', focus: true, createdAt: new Date().toISOString() },
        { id: uid(), title: '观察 19:30 飞书自动推送', description: '时间管理自动化保持不动，只观察是否稳定到达。', priority: 'P1', status: 'waiting', project: '时间管理', due: today?.date || '', focus: false, createdAt: new Date().toISOString() },
      ];
      writeLocal(WORKBENCH_TASK_KEY, defaults);
      return defaults;
    }
    function saveTasks(tasks) { writeLocal(WORKBENCH_TASK_KEY, tasks); }
    function loadReviews() { return readLocal(WORKBENCH_REVIEW_KEY, {}); }
    function saveReviews(reviews) { writeLocal(WORKBENCH_REVIEW_KEY, reviews); }
    function activeTasks() { return loadTasks().filter(task => task.status !== 'done').sort((a,b) => (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9)); }
    function taskStats(tasks) {
      const pending = tasks.filter(t => t.status !== 'done');
      const done = tasks.filter(t => t.status === 'done').length;
      const counts = Object.fromEntries(Object.keys(statusStyles).map(key => [key, tasks.filter(t => t.status === key).length]));
      const isUrgent = (task) => task.priority === 'P0' || task.status === 'blocked';
      const urgent = pending.filter(isUrgent).length;
      const doing = pending.filter(t => !isUrgent(t) && t.status === 'doing').length;
      const todo = pending.filter(t => !isUrgent(t) && t.status === 'todo').length;
      const waiting = pending.filter(t => !isUrgent(t) && t.status === 'waiting').length;
      const high = pending.filter(t => ['P0','P1'].includes(t.priority)).length;
      const todayFocus = pending.filter(t => t.focus).length;
      return { total: tasks.length, pending: pending.length, done, counts, urgent, high, todayFocus, blocked: tasks.filter(t => t.status === 'blocked').length, doing, todo, waiting };
    }
    function statusDonutStyle(stats) {
      const total = Math.max(stats.pending, 1);
      let cursor = 0;
      const segments = ['urgent', 'doing', 'todo', 'waiting'].map(key => {
        const count = stats[key] || 0;
        const start = cursor;
        cursor += (count / total) * 100;
        return `${statusStyles[key].color} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
      });
      if (stats.pending === 0) return 'background: conic-gradient(#63627f 0% 100%);';
      return `background: conic-gradient(${segments.join(', ')});`;
    }
    function renderStatusLegend(stats) {
      return ['urgent', 'doing', 'todo', 'waiting'].map(key => `
        <span class="legend-pill"><i class="dot" style="background:${statusStyles[key].color}"></i>${statusStyles[key].label} ${stats[key] || 0}</span>
      `).join('');
    }

    window.addTask = function addTask() {
      const titleInput = document.getElementById('taskTitle');
      const title = titleInput.value.trim();
      if (!title) return;
      const tasks = loadTasks();
      tasks.push({
        id: uid(), title,
        description: document.getElementById('taskDescription').value.trim(),
        priority: document.getElementById('taskPriority').value,
        status: document.getElementById('taskStatus').value,
        project: document.getElementById('taskProject').value.trim() || '未归属',
        due: document.getElementById('taskDue').value,
        focus: document.getElementById('taskFocus').checked,
        createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
      });
      saveTasks(tasks);
      renderWorkbench();
    };
    window.openTaskModal = function openTaskModal() {
      const modal = document.getElementById('taskModal');
      if (modal) modal.classList.add('open');
    };
    window.closeTaskModal = function closeTaskModal() {
      const modal = document.getElementById('taskModal');
      if (modal) modal.classList.remove('open');
    };
    window.updateTaskStatus = function updateTaskStatus(id, status) {
      const tasks = loadTasks().map(task => task.id === id ? {...task, status, updatedAt: new Date().toISOString()} : task);
      saveTasks(tasks); renderWorkbench();
    };
    window.toggleTaskFocus = function toggleTaskFocus(id) {
      const tasks = loadTasks().map(task => task.id === id ? {...task, focus: !task.focus, updatedAt: new Date().toISOString()} : task);
      saveTasks(tasks); renderWorkbench();
    };
    window.deleteTask = function deleteTask(id) {
      if (!confirm('确认删除这条待办？')) return;
      saveTasks(loadTasks().filter(task => task.id !== id));
      renderWorkbench();
    };
    window.saveReview = function saveReview() {
      if (!today) return;
      const reviews = loadReviews();
      reviews[today.date] = {
        completed: document.getElementById('reviewCompleted').value.trim(),
        progress: document.getElementById('reviewProgress').value.trim(),
        blocked: document.getElementById('reviewBlocked').value.trim(),
        tomorrow: document.getElementById('reviewTomorrow').value.trim(),
        focusScore: document.getElementById('reviewFocus').value,
        satisfactionScore: document.getElementById('reviewSatisfaction').value,
        updatedAt: new Date().toISOString(),
      };
      saveReviews(reviews);
      renderWorkbench();
    };


    let mode = 'workbench';
    let currentKey = days[0]?.date || weeks[0]?.id;
    let deadlineViewMode = localStorage.getItem('deadlineViewMode') || 'list';

    function switchMode(next) {
      mode = next;
      tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.tab === next));
      dayList.classList.toggle('active', next === 'days');
      weekList.classList.toggle('active', next === 'weeks');
      currentKey = next === 'days' ? (days[0]?.date) : (next === 'weeks' ? weeks[0]?.id : days[0]?.date);
      renderNav();
      renderContent();
    }

    function renderNav() {
      dayList.innerHTML = days.map(day => `
        <button class="item ${currentKey === day.date && mode === 'days' ? 'active' : ''}" data-kind="day" data-key="${day.date}">
          <strong>${day.date} · ${day.weekday}</strong>
          <small>${day.total_active_text} · ${(day.top_categories || []).join(' / ') || '暂无分类'}</small>
        </button>
      `).join('');
      weekList.innerHTML = weeks.map(week => `
        <button class="item ${currentKey === week.id && mode === 'weeks' ? 'active' : ''}" data-kind="week" data-key="${week.id}">
          <strong>${week.label}</strong>
          <small>${week.total_active_text} · ${(week.categories || []).slice(0,3).map(c => c.name).join(' / ') || '暂无分类'}</small>
        </button>
      `).join('');
      document.querySelectorAll('.item').forEach(node => node.onclick = () => {
        currentKey = node.dataset.key;
        mode = node.dataset.kind === 'day' ? 'days' : 'weeks';
        tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.tab === mode));
        dayList.classList.toggle('active', mode === 'days');
        weekList.classList.toggle('active', mode === 'weeks');
        renderNav();
        renderContent();
      });
    }

    function renderTasks(tasks) {
      const sorted = [...tasks].sort((a,b) => (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9));
      if (!sorted.length) return '<div class="empty">暂无待办。先添加今天要推进的事项。</div>';
      return `<div class="task-list">${sorted.map(task => `
        <div class="task-card ${task.status === 'done' ? 'done' : ''}">
          <input type="checkbox" ${task.status === 'done' ? 'checked' : ''} onchange="updateTaskStatus('${task.id}', this.checked ? 'done' : 'doing')" />
          <div class="task-main">
            <strong>${escapeHtml(task.title)}</strong>
            <p>${escapeHtml(task.description || '暂无描述')}</p>
            <div class="task-meta">
              <span class="badge p-${task.priority}">${task.priority}</span>
              <span class="badge">${statusLabels[task.status] || task.status}</span>
              <span class="badge">${escapeHtml(task.project || '未归属')}</span>
              ${task.due ? `<span class="badge">截止 ${escapeHtml(task.due)}</span>` : ''}
              ${task.focus ? '<span class="badge p-P1">今日重点</span>' : ''}
            </div>
          </div>
          <div class="toolbar">
            <button class="btn" onclick="toggleTaskFocus('${task.id}')">${task.focus ? '取消重点' : '设为重点'}</button>
            <select onchange="updateTaskStatus('${task.id}', this.value)">
              ${Object.entries(statusLabels).map(([key,label]) => `<option value="${key}" ${task.status === key ? 'selected' : ''}>${label}</option>`).join('')}
            </select>
            <button class="btn danger" onclick="deleteTask('${task.id}')">删除</button>
          </div>
        </div>`).join('')}</div>`;
    }

    function renderReviewEditor(review) {
      return `
        <div class="review-grid">
          <label>今日完成<textarea id="reviewCompleted" placeholder="今天真正完成了什么？">${escapeHtml(review.completed || '')}</textarea></label>
          <label>推进中<textarea id="reviewProgress" placeholder="推进了但还没完成的事情。">${escapeHtml(review.progress || '')}</textarea></label>
          <label>阻塞/风险<textarea id="reviewBlocked" placeholder="卡点、风险、需要明天处理的中断。">${escapeHtml(review.blocked || '')}</textarea></label>
          <label>明日下一步<textarea id="reviewTomorrow" placeholder="明天第一步做什么？">${escapeHtml(review.tomorrow || '')}</textarea></label>
          <label>专注度<select id="reviewFocus">${[1,2,3,4,5].map(n => `<option value="${n}" ${String(review.focusScore || 3) === String(n) ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
          <label>满意度<select id="reviewSatisfaction">${[1,2,3,4,5].map(n => `<option value="${n}" ${String(review.satisfactionScore || 3) === String(n) ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
          <div class="wide toolbar"><button class="btn primary" onclick="saveReview()">保存今日复盘</button><span class="subtle">数据保存在当前浏览器 localStorage，不影响 19:30 自动推送。</span></div>
        </div>`;
    }

    function deadlineLabel(due) {
      if (!due) return '无截止';
      const base = today?.date || new Date().toISOString().slice(0, 10);
      const diff = Math.round((new Date(`${due}T00:00:00`) - new Date(`${base}T00:00:00`)) / 86400000);
      if (diff < 0) return `已逾期 ${Math.abs(diff)} 天`;
      if (diff === 0) return '今天';
      if (diff === 1) return '明天';
      if (diff <= 30) return `${diff} 天后`;
      return due;
    }

    function renderDeadlineTimeline(tasks) {
      const pending = tasks
        .filter(task => task.status !== 'done')
        .sort((a, b) => (a.due || '9999-12-31').localeCompare(b.due || '9999-12-31') || (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9));
      if (!pending.length) return '<div class="empty">暂无待推进事项。可以点击待办列表底部按钮新增。</div>';
      return `<div class="deadline-timeline">${pending.map(task => `
        <div class="deadline-row">
          <div class="deadline-date">${deadlineLabel(task.due)}<br>${escapeHtml(task.due || '')}</div>
          <div class="deadline-body">
            <strong>${escapeHtml(task.title)}</strong>
            <span class="badge p-${task.priority}">${task.priority}</span>
            <span class="badge">${statusLabels[task.status] || task.status}</span>
            <span class="badge">${escapeHtml(task.project || '未归属')}</span>
            ${task.focus ? '<span class="badge p-P1">今日重点</span>' : ''}
          </div>
        </div>`).join('')}</div>`;
    }

    function addDays(dateStr, offset) {
      const [year, month, day] = dateStr.split('-').map(Number);
      const date = new Date(Date.UTC(year, month - 1, day + offset));
      return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`;
    }

    function renderDeadlineCalendar(tasks) {
      const pending = tasks.filter(task => task.status !== 'done');
      const base = today?.date || new Date().toISOString().slice(0, 10);
      const daysToShow = Array.from({length: 14}, (_, index) => addDays(base, index));
      return `<div class="deadline-calendar">${daysToShow.map(day => {
        const dayTasks = pending.filter(task => task.due === day).sort((a,b) => (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9));
        return `<div class="calendar-day"><strong>${deadlineLabel(day)} · ${day.slice(5)}</strong>${dayTasks.map(task => `
          <span class="calendar-task"><b>${task.priority}</b> ${escapeHtml(task.title)}<span class="countdown">距截止：${deadlineLabel(task.due)}</span></span>
        `).join('')}</div>`;
      }).join('')}</div>`;
    }

    window.setDeadlineView = function setDeadlineView(next) {
      deadlineViewMode = next;
      localStorage.setItem('deadlineViewMode', next);
      renderWorkbench();
    };

    function renderDeadlineView(tasks) {
      return `
        <div class="view-switch">
          <button class="${deadlineViewMode === 'list' ? 'active' : ''}" onclick="setDeadlineView('list')">列表视图</button>
          <button class="${deadlineViewMode === 'calendar' ? 'active' : ''}" onclick="setDeadlineView('calendar')">日历视图</button>
        </div>
        ${deadlineViewMode === 'calendar' ? renderDeadlineCalendar(tasks) : renderDeadlineTimeline(tasks)}
      `;
    }

    function renderTaskReminders(day, tasks, stats) {
      const insights = [];
      if (stats.blocked) insights.push(`有 ${stats.blocked} 个阻塞任务，建议优先写清“下一步动作”或外部依赖。`);
      if (stats.high >= 3) insights.push(`P0/P1 高优任务共有 ${stats.high} 个，今天最好只选 1–3 个作为真正主线。`);
      if (day?.switch_count >= 120) insights.push(`今天窗口切换 ${day.switch_count} 次，时间复盘显示上下文切换偏高。`);
      if (day) insights.push(`今日时间主轴是“${day.top_categories[0] || '暂无'}”，可以和待办主线对照，判断是否跑偏。`);
      if (!insights.length) insights.push('当前任务负荷可控，重点是持续记录完成情况和明日第一步。');
      return `<div class="insight-list">${insights.map(item => `<div class="insight">${item}</div>`).join('')}</div>`;
    }

    function renderWorkbench() {
      const tasks = loadTasks();
      const stats = taskStats(tasks);
      const reviews = loadReviews();
      const review = today ? (reviews[today.date] || {}) : {};
      title.textContent = '总览';
      subtitle.textContent = '首屏只回答一个问题：现在还有多少内容待推进。下面再继续处理任务、复盘和时间数据。';
      meta.innerHTML = today ? `${today.date} · ${today.weekday}<br>每日 19:30 自动复盘保持不动` : '等待时间数据生成';
      content.innerHTML = `
        <article class="card span-12 overview-first-fold">
          <div class="overview-top">
            <div class="overview-kicker">Personal Workbench Overview</div>
            <div class="donut" style="${statusDonutStyle(stats)}">
              <div class="donut-center"><div class="donut-number">${stats.pending}</div><div class="donut-label">未完成事项</div></div>
            </div>
            <div class="status-legend">${renderStatusLegend(stats)}</div>
          </div>
          <div class="overview-metrics">
            <div class="metric-card"><label>待推进总数</label><strong>${stats.pending}<span class="unit">项</span></strong><p>当前仍需推进</p></div>
            <div class="metric-card"><label>紧急</label><strong style="color:${statusStyles.urgent.color}">${stats.urgent}<span class="unit">项</span></strong><p>今天或明天要处理</p></div>
            <div class="metric-card"><label>今日重点</label><strong style="color:${statusStyles.doing.color}">${stats.todayFocus}<span class="unit">个</span></strong><p>已标记优先推进</p></div>
            <div class="metric-card"><label>今日活跃</label><strong>${today?.total_active_text || '—'}</strong><p>时间管理板块数据</p></div>
          </div>
        </article>
        <article class="card span-8 next-fold"><h3 class="section-title">今日重点与待办中心</h3>${renderTasks(tasks)}
          <button class="add-task-trigger" onclick="openTaskModal()">＋ 新增待办</button>
        </article>
        <div class="task-modal-backdrop" id="taskModal" onclick="if (event.target === this) closeTaskModal()">
          <div class="task-modal" role="dialog" aria-modal="true" aria-labelledby="taskModalTitle">
            <div class="task-modal-head">
              <div><h3 id="taskModalTitle">新增待办</h3><p>这是一个强操作入口，填写完成后会回到待办中心。</p></div>
              <button class="icon-close" onclick="closeTaskModal()" aria-label="关闭">×</button>
            </div>
            <div class="form-grid">
              <label>标题<input id="taskTitle" placeholder="例如：完成首页布局" /></label>
              <label>优先级<select id="taskPriority"><option>P0</option><option selected>P1</option><option>P2</option><option>P3</option></select></label>
              <label>状态<select id="taskStatus"><option value="todo">待办</option><option value="doing" selected>进行中</option><option value="waiting">等待中</option><option value="blocked">阻塞中</option></select></label>
              <label>截止<input id="taskDue" type="date" value="${today?.date || ''}" /></label>
              <label>项目<input id="taskProject" placeholder="项目/板块" value="个人工作台" /></label>
              <label class="wide">描述<input id="taskDescription" placeholder="补充下一步动作、验收标准或背景" /></label>
              <label><input id="taskFocus" type="checkbox" checked /> 今日重点</label>
              <div class="toolbar"><button class="btn primary" onclick="addTask()">添加待办</button><button class="btn" onclick="closeTaskModal()">取消</button></div>
            </div>
          </div>
        </div>
        <article class="card span-4 next-fold"><h3 class="section-title">任务提醒</h3>${renderTaskReminders(today, tasks, stats)}</article>
        <article class="card span-12"><h3 class="section-title">任务截止时间轴</h3>${renderDeadlineView(tasks)}</article>
        <article class="card span-6"><h3 class="section-title">今日复盘</h3>${renderReviewEditor(review)}</article>
        <article class="card span-12"><h3 class="section-title">时间与精力板块预览</h3><div class="kpis">
          <div class="kpi"><label>总活跃时长</label><strong>${today?.total_active_text || '—'}</strong></div>
          <div class="kpi"><label>主类目</label><strong>${today?.top_categories?.[0] || '—'}</strong></div>
          <div class="kpi"><label>窗口切换</label><strong>${today?.switch_count ?? '—'}</strong></div>
          <div class="kpi"><label>最长专注</label><strong>${today?.longest_focus?.text || '—'}</strong></div>
        </div><p class="subtle" style="margin-top:14px;">完整时间详情请点击左侧“时间与精力”。每日 19:30 飞书推送逻辑未改动。</p></article>
      `;
    }

    function renderDay(day) {
      title.textContent = `每日复盘 · ${day.date} · ${day.weekday}`;
      subtitle.textContent = day.summary_line;
      meta.innerHTML = `总活跃：${day.total_active_text}<br>切换：${day.switch_count} 次<br>最长专注：${day.longest_focus.text}`;
      const maxHour = Math.max(...Object.values(day.hourly_seconds || {}), 1);
      content.innerHTML = `
        <article class="card span-12"><div class="kpis">
          <div class="kpi"><label>总活跃时长</label><strong>${day.total_active_text}</strong></div>
          <div class="kpi"><label>主类目</label><strong>${day.top_categories[0] || '—'}</strong></div>
          <div class="kpi"><label>窗口切换</label><strong>${day.switch_count}</strong></div>
          <div class="kpi"><label>结论置信度</label><strong>${day.energy.confidence}</strong></div>
        </div></article>
        <article class="card span-8"><h3 class="section-title">类目时间分布</h3><div class="bars">
          ${(day.categories || []).map(cat => `<div class="bar-row"><div>${cat.name}</div><div class="bar"><span style="width:${pct(cat.share)}; background:${cat.color};"></span></div><div>${fmtDuration(cat.seconds)} · ${pct(cat.share)}</div></div>`).join('')}
        </div></article>
        <article class="card span-4"><h3 class="section-title">精力判断</h3><div class="chips">
          <span class="chip">高投入：${day.energy.high_zone}</span><span class="chip">碎片区：${day.energy.fragmented_zone}</span><span class="chip">恢复区：${day.energy.recovery_zone}</span>
        </div><ul class="note-list" style="margin-top:12px;">${(day.energy.evidence || []).map(item => `<li>${item}</li>`).join('')}</ul></article>
        <article class="card span-12"><h3 class="section-title">小时节奏</h3><div class="timeline">
          ${Object.entries(day.hourly_seconds || {}).map(([hour, seconds]) => `<div class="hour" style="height:${Math.max((seconds / maxHour) * 100, 4)}%; opacity:${seconds ? 1 : 0.22};"><span>${hour}</span></div>`).join('')}
        </div></article>
        <article class="card span-6"><h3 class="section-title">代表活动</h3><table><thead><tr><th>活动</th><th>时长</th></tr></thead><tbody>
          ${(day.top_activities || []).map(item => `<tr><td>${item.activity}</td><td>${item.text}</td></tr>`).join('')}
        </tbody></table></article>
        <article class="card span-6"><h3 class="section-title">复盘提示</h3><ul class="note-list">
          ${(day.notes || []).map(item => `<li>${item}</li>`).join('')}
        </ul><p class="subtle" style="margin-top:16px;">最长连续专注块：${day.longest_focus.range} · ${day.longest_focus.activity}</p></article>
      `;
    }

    function renderWeek(week) {
      title.textContent = `周总结 · ${week.label}`;
      subtitle.textContent = week.summary_line;
      meta.innerHTML = `统计区间：${week.start_date} ～ ${week.end_date}<br>总活跃：${week.total_active_text}<br>纳入天数：${week.daily_totals.length} 天`;
      const maxDay = Math.max(...(week.daily_totals || []).map(item => item.seconds), 1);
      const topDay = [...(week.daily_totals || [])].sort((a,b)=>b.seconds-a.seconds)[0];
      content.innerHTML = `
        <article class="card span-12"><div class="kpis">
          <div class="kpi"><label>周总活跃</label><strong>${week.total_active_text}</strong></div>
          <div class="kpi"><label>主类目</label><strong>${week.categories[0]?.name || '—'}</strong></div>
          <div class="kpi"><label>活跃最高日</label><strong>${topDay?.date || '—'}</strong></div>
          <div class="kpi"><label>累计日数</label><strong>${week.daily_totals.length}</strong></div>
        </div></article>
        <article class="card span-7"><h3 class="section-title">本周类目占比</h3><div class="bars">
          ${(week.categories || []).map(cat => `<div class="bar-row"><div>${cat.name}</div><div class="bar"><span style="width:${pct(cat.share)}; background:${cat.color};"></span></div><div>${fmtDuration(cat.seconds)} · ${pct(cat.share)}</div></div>`).join('')}
        </div></article>
        <article class="card span-5"><h3 class="section-title">周观察</h3><ul class="note-list">${(week.notes || []).map(item => `<li>${item}</li>`).join('')}</ul></article>
        <article class="card span-12"><h3 class="section-title">每日总量</h3><div class="bars">
          ${(week.daily_totals || []).map(item => `<div class="bar-row"><div>${item.date}</div><div class="bar"><span style="width:${(item.seconds / maxDay * 100).toFixed(1)}%; background:#7aa2ff;"></span></div><div>${item.text}</div></div>`).join('')}
        </div></article>
      `;
    }

    function renderContent() {
      if (mode === 'workbench') {
        renderWorkbench();
      } else if (mode === 'days') {
        const day = days.find(item => item.date === currentKey) || days[0];
        if (!day) return;
        renderDay(day);
      } else {
        const week = weeks.find(item => item.id === currentKey) || weeks[0];
        if (!week) return;
        renderWeek(week);
      }
    }

    tabs.forEach(tab => tab.onclick = () => switchMode(tab.dataset.tab));
    renderNav();
    renderContent();
  </script>
</body>
</html>'''
    return template.replace("__STORE_JSON__", json.dumps(history, ensure_ascii=False))


def write_site(history: dict[str, Any]) -> None:
    INDEX_PATH.write_text(render_html(history), encoding="utf-8")
    NOJEKYLL_PATH.write_text("", encoding="utf-8")
    DOCS_INDEX_PATH.write_text(render_html(history), encoding="utf-8")
    DOCS_NOJEKYLL_PATH.write_text("", encoding="utf-8")

def build_message(day_record: dict[str, Any], week_record: dict[str, Any] | None, git_status: dict[str, Any]) -> str:
    lines = [
        f"# 电脑时间复盘｜{day_record['date']}（{day_record['weekday']}）",
        "",
        f"- 总活跃时长：{day_record['total_active_text']}",
        f"- 主要类目：{' / '.join(day_record['top_categories'][:3]) if day_record['top_categories'] else '暂无'}",
        f"- 今日判断：{day_record['summary_line']}",
        f"- 最长专注块：{day_record['longest_focus']['text']} · {day_record['longest_focus']['range']}",
    ]
    if week_record:
        lines.extend([
            f"- 周总结：{week_record['label']} 累计 {week_record['total_active_text']}，主轴是“{week_record['categories'][0]['name']}”。" if week_record.get('categories') else f"- 周总结：{week_record['label']} 累计 {week_record['total_active_text']}。",
        ])
    lines.extend([
        f"- 同步状态：{git_status['message']}",
        "",
        f"MEDIA:{INDEX_PATH}",
    ])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    now = local_now(args)
    history, state = update_history(now, rebuild_days=args.rebuild_days, force_date=args.force_date)
    if args.mark_sent_date:
        mark_sent_by_date(history, state, args.mark_sent_date)
        return 0
    write_site(history)
    git_status = maybe_commit_and_push(now, skip_push=args.skip_push)
    day_record = None
    if args.force_date:
        target = args.force_date
        day_record = next((item for item in history["days"] if item["date"] == target), None)
    else:
        day_record = eligible_unsent_day(history, state, now)
    if not day_record:
        if args.print_dry_run:
            print("[SILENT]")
        return 0
    week_record = corresponding_week(history, day_record)
    print(build_message(day_record, week_record, git_status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
