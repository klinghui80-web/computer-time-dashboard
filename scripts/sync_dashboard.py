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
      --bg: #000000;
      --glass-bg: rgba(255,255,255,.072);
      --glass-bg-strong: rgba(255,255,255,.095);
      --glass-border: rgba(255,255,255,.13);
      --glass-highlight: rgba(255,255,255,.105);
      --panel: var(--glass-bg);
      --panel-2: rgba(255,255,255,.052);
      --line: rgba(255,255,255,.105);
      --text: #eff4ff;
      --muted: #9fb0d6;
    }
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--text); background:#000000; font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif; }
    .shell { display: grid; grid-template-columns: 310px 1fr; min-height: 100vh; }
    .sidebar { padding: 24px 18px; border-right: 1px solid var(--line); background: rgba(255,255,255,0.032); backdrop-filter: blur(24px) saturate(118%); -webkit-backdrop-filter: blur(24px) saturate(118%); position: sticky; top: 0; height: 100vh; overflow: auto; }
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
    .card { background: linear-gradient(145deg, var(--glass-bg-strong), rgba(255,255,255,.034)); border: 1px solid var(--glass-border); border-radius: 28px; padding: 20px; box-shadow: inset 0 1px 0 var(--glass-highlight), 0 22px 58px rgba(0,0,0,.42); backdrop-filter: blur(24px) saturate(118%); -webkit-backdrop-filter: blur(24px) saturate(118%); }
    .span-12 { grid-column: span 12; }
    .span-8 { grid-column: span 8; }
    .span-6 { grid-column: span 6; }
    .span-7 { grid-column: span 7; }
    .span-5 { grid-column: span 5; }
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
    .overview-first-fold { min-height: calc(100vh - 112px); display:flex; flex-direction:column; justify-content:center; background:radial-gradient(circle at 34% 48%, rgba(122,92,255,.13), transparent 34%), linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.01)); overflow:hidden; }
    .overview-dashboard { min-height:720px; display:grid; grid-template-columns:minmax(0,1fr) minmax(340px,.38fr); gap:22px; align-items:stretch; }
    .overview-left-stage { position:relative; min-height:720px; }
    .overview-greeting { position:absolute; z-index:11; left:24px; top:18px; margin:0; font-size:44px; line-height:1.08; letter-spacing:-1.2px; font-weight:780; color:#f7f8f8; text-shadow:0 10px 40px rgba(0,0,0,.34); }
    .overview-orbit-map { position:relative; min-height:700px; height:100%; border-radius:0; overflow:visible; isolation:isolate; background:transparent; box-shadow:none; }
    .overview-left-stage .orbit-map-stage { transform:translateX(-1%); }
    .orbit-map-stage { position:absolute; inset:0; z-index:2; }
    .liquid-glass { background:linear-gradient(145deg, rgba(255,255,255,.082), rgba(255,255,255,.036)); border:1px solid var(--glass-border); backdrop-filter: blur(22px) saturate(118%); -webkit-backdrop-filter: blur(22px) saturate(118%); box-shadow: inset 0 1px 0 rgba(255,255,255,.10), 0 18px 54px rgba(0,0,0,.42); }
    .orbit-connector-layer { position:absolute; left:50%; top:50%; width:1px; height:1px; z-index:4; overflow:visible; pointer-events:none; }
    .orbit-connector-path { fill:none; stroke:var(--cat-color,#7aa2ff); stroke-width:3.5; stroke-linecap:round; opacity:.82; filter:drop-shadow(0 0 10px var(--cat-color,#7aa2ff)); stroke-dasharray:620; stroke-dashoffset:620; animation: orbit-line-draw .95s ease forwards; animation-delay:var(--delay,0ms); }
    .orbit-connector-particles { fill:none; stroke:rgba(255,255,255,.86); stroke-width:4.2; stroke-linecap:round; opacity:.92; filter:drop-shadow(0 0 12px var(--cat-color,#7aa2ff)); stroke-dasharray:1 13; stroke-dashoffset:96; animation: orbit-particles 2.8s linear infinite, orbit-line-draw .95s ease forwards; animation-delay:var(--delay,0ms), var(--delay,0ms); }
    .orbit-core { position:absolute; left:50%; top:50%; z-index:6; width:124px; height:124px; border-radius:50%; transform:translate(-50%,-50%); display:grid; place-items:center; text-align:center; color:#fff; background:radial-gradient(circle at 38% 30%, rgba(255,255,255,.24), rgba(122,92,255,.38) 42%, rgba(37,22,75,.70)); border:1px solid rgba(255,255,255,.14); box-shadow:0 0 0 54px rgba(122,92,255,.08), 0 0 0 108px rgba(122,92,255,.032), 0 30px 100px rgba(122,92,255,.24); }
    .orbit-core::before { content:""; position:absolute; inset:-96px; border-radius:50%; border:1px solid rgba(255,255,255,.07); }
    .orbit-core::after { content:""; position:absolute; inset:-48px; border-radius:50%; border:1px solid rgba(255,255,255,.1); }
    .orbit-core-inner { position:relative; z-index:9; display:grid; place-items:center; }
    .orbit-count { font-size:78px; line-height:.9; letter-spacing:-2px; font-weight:750; text-shadow:0 2px 26px rgba(0,0,0,.35); }
    .orbit-task-card { position:absolute; left:50%; top:50%; z-index:8; width:220px; min-height:98px; border-radius:22px; padding:14px 15px; color:#f7f8f8; background:linear-gradient(135deg, rgba(17,24,39,.94), rgba(20,30,52,.84)) !important; border:1px solid color-mix(in srgb, var(--cat-color), rgba(255,255,255,.14) 44%); transform:translate(calc(-50% + var(--orbit-x)), calc(-50% + var(--orbit-y))) scale(1); animation: orbit-card-bloom .78s cubic-bezier(.2,.8,.2,1) both; animation-delay:var(--delay,0ms); box-shadow: inset 0 1px 0 rgba(255,255,255,.11), 0 18px 54px rgba(0,0,0,.42), 0 0 30px color-mix(in srgb, var(--cat-color), transparent 70%); isolation:isolate; }
    .orbit-task-card::before { content:""; position:absolute; inset:-1px; border-radius:inherit; background:linear-gradient(135deg, var(--cat-color), transparent 42%, rgba(255,255,255,.10)); opacity:.50; z-index:0; }
    .orbit-task-card > * { position:relative; z-index:1; }
    .orbit-task-card .orbit-task-head { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:8px; }
    .orbit-task-card b { display:block; font-size:16px; line-height:1.25; letter-spacing:-.18px; text-shadow:0 1px 12px rgba(0,0,0,.45); }
    .orbit-task-card small { color:#8a8f98; font-family:'JetBrains Mono', ui-monospace, monospace; font-size:11px; white-space:nowrap; }
    .orbit-category-dot { width:9px; height:9px; border-radius:50%; background:var(--cat-color); box-shadow:0 0 16px var(--cat-color); flex:0 0 auto; }
    .orbit-task-meta { display:flex; gap:7px; flex-wrap:wrap; margin-top:10px; color:#d0d6e0; font-size:11px; }
    .orbit-task-meta span { border:1px solid rgba(255,255,255,.09); background:rgba(255,255,255,.05); border-radius:999px; padding:4px 7px; }
    .orbit-legend { position:absolute; left:22px; bottom:20px; z-index:9; display:flex; gap:8px; flex-wrap:wrap; }
    .legend-pill { display:inline-flex; align-items:center; gap:8px; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.05); color:#d0d6e0; border-radius:999px; padding:8px 12px; font-size:12px; }
    .dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
    .orbit-empty { position:absolute; left:50%; top:50%; z-index:4; transform:translate(-50%, 96px); color:#8a8f98; }
    @keyframes orbit-card-bloom { from { opacity:0; transform:translate(-50%,-50%) scale(.35); filter:blur(12px); } to { opacity:1; transform:translate(calc(-50% + var(--orbit-x)), calc(-50% + var(--orbit-y))) scale(1); filter:blur(0); } }
    @keyframes orbit-line-draw { to { stroke-dashoffset:0; } }
    @keyframes orbit-particles { to { stroke-dashoffset:-180; } }
    @media (prefers-reduced-motion: reduce) { .orbit-task-card, .orbit-connector-path, .orbit-connector-particles { animation:none; } }
    .overview-side-panel { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); grid-template-rows:290px 210px auto; gap:16px; min-height:720px; }
    .glass-card { position:relative; overflow:hidden; border-radius:26px; padding:20px; background:linear-gradient(145deg, var(--glass-bg-strong), rgba(255,255,255,.038)); border:1px solid var(--glass-border); box-shadow:inset 0 1px 0 var(--glass-highlight), 0 22px 58px rgba(0,0,0,.40); backdrop-filter: blur(24px) saturate(118%); -webkit-backdrop-filter: blur(24px) saturate(118%); color:#f7f8f8; }
    .glass-card::before { content:""; position:absolute; inset:0; background:radial-gradient(circle at 20% 8%, rgba(255,255,255,.12), transparent 30%), linear-gradient(135deg, rgba(255,255,255,.06), transparent 46%); pointer-events:none; }
    .glass-card > * { position:relative; z-index:1; }
    .side-card-wide { grid-column:span 2; }
    .glass-card h4 { margin:0; font-size:15px; letter-spacing:-.12px; color:#eef4ff; }
    .glass-card .glass-subtitle { color:rgba(223,231,255,.68); font-size:12px; margin-top:4px; }
    .calendar-card { min-height:290px; }
    .calendar-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:18px; }
    .calendar-month-title { flex:1; text-align:center; font-size:24px; line-height:1; font-weight:780; letter-spacing:-.6px; }
    .calendar-nav { width:34px; height:34px; border-radius:999px; display:grid; place-items:center; border:1px solid rgba(255,255,255,.12); background:rgba(0,0,0,.18); color:#fff; font-size:24px; line-height:1; }
    .calendar-weekdays { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:6px; color:rgba(255,255,255,.42); font-size:12px; font-weight:700; text-align:center; margin-bottom:10px; }
    .calendar-month-grid { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:7px 6px; }
    .calendar-day-cell { height:28px; border-radius:999px; display:grid; place-items:center; color:#fff; font-size:16px; font-weight:650; }
    .calendar-day-cell.muted { color:rgba(255,255,255,.20); }
    .calendar-day-cell.active { background:linear-gradient(135deg, #8b5cf6, #6d5dfc); box-shadow:0 10px 28px rgba(139,92,246,.40); }
    .time-line-card, .category-donut-card { min-height:0; height:210px; }
    .line-chart-svg { width:100%; height:74px; margin-top:8px; overflow:visible; }
    .line-chart-fill { fill:rgba(122,162,255,.16); }
    .line-chart-path { fill:none; stroke:#77b7ff; stroke-width:4; stroke-linecap:round; stroke-linejoin:round; filter:drop-shadow(0 0 10px rgba(119,183,255,.42)); }
    .line-chart-axis { stroke:rgba(255,255,255,.12); stroke-width:1; }
    .glass-big-number { font-size:24px; line-height:1; margin-top:10px; font-weight:760; letter-spacing:-.8px; white-space:nowrap; }
    .side-donut-wrap { display:flex; align-items:center; gap:8px; margin-top:14px; }
    .side-donut { width:72px; height:72px; border-radius:50%; display:grid; place-items:center; background:conic-gradient(#7aa2ff 0% 100%); box-shadow:inset 0 0 0 12px rgba(255,255,255,.09), 0 16px 38px rgba(0,0,0,.24); flex:0 0 72px; }
    .side-donut::after { content:""; width:40px; height:40px; border-radius:50%; background:rgba(16,24,40,.78); border:1px solid rgba(255,255,255,.1); }
    .side-legend { display:grid; gap:7px; flex:1; min-width:0; }
    .side-legend-row { display:flex; align-items:center; justify-content:space-between; gap:6px; color:rgba(223,231,255,.78); font-size:10px; }
    .side-legend-row span { display:flex; align-items:center; min-width:0; white-space:nowrap; }
    .side-legend-row b { white-space:nowrap; }
    .side-legend-row i { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:6px; }
    .carryover-card { min-height:188px; }
    .carryover-list { display:grid; gap:10px; margin-top:14px; }
    .carryover-item { border-radius:15px; padding:11px 12px; background:rgba(255,255,255,.075); border:1px solid rgba(255,255,255,.09); color:#eef4ff; }
    .carryover-item small { display:block; margin-top:5px; color:rgba(223,231,255,.6); }
    .next-fold { margin-top:4px; }
    .toolbar { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    .btn { border:1px solid rgba(255,255,255,0.08); color:var(--text); background:rgba(255,255,255,0.04); border-radius:10px; padding:9px 12px; cursor:pointer; font:inherit; }
    .btn.primary { background:#5e6ad2; border-color:#7170ff; color:#fff; }
    .btn.danger { color:#ffb4b4; border-color:rgba(255,125,125,.35); }
    .field, select, textarea, input:not([type="checkbox"]) { width:100%; color:var(--text); background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:12px; padding:10px 12px; font:inherit; outline:none; }
    input[type="checkbox"] { accent-color:#7170ff; width:16px; height:16px; margin-top:4px; }
    textarea { min-height:86px; resize:vertical; line-height:1.6; }
    .form-grid { display:grid; grid-template-columns: 1.2fr .55fr .55fr .7fr; gap:10px; align-items:end; }
    .task-modal .form-grid { grid-template-columns:1.3fr .65fr .65fr .75fr; gap:14px; }
    .form-grid .wide { grid-column: span 2; }
    .form-grid label, .review-grid label { display:grid; gap:6px; font-size:12px; color:var(--muted); }
    .task-list { display:grid; gap:14px; }
    .plane-view-toolbar { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-bottom:16px; padding:10px 12px; border:1px solid rgba(255,255,255,.07); border-radius:16px; background:rgba(255,255,255,.025); }
    .view-title { display:flex; align-items:center; gap:10px; color:#f7f8f8; font-weight:650; letter-spacing:-.18px; }
    .view-title .view-icon { width:30px; height:30px; display:grid; place-items:center; border-radius:9px; background:rgba(122,162,255,.12); border:1px solid rgba(122,162,255,.22); color:#a9c3ff; }
    .view-chips { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
    .view-chip { border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.03); color:#8a8f98; border-radius:9px; padding:7px 10px; font-size:12px; font:inherit; cursor:pointer; }
    .view-chip.active, .view-chip[aria-pressed="true"] { color:#f7f8f8; background:rgba(122,162,255,.14); border-color:rgba(122,162,255,.4); }
    .view-chip:hover { color:#f7f8f8; border-color:rgba(122,162,255,.34); }
    .priority-lane { display:grid; gap:10px; border:1px solid rgba(255,255,255,.06); border-radius:20px; padding:12px; background:rgba(255,255,255,.018); }
    .priority-lane.drop-same { border-color:rgba(122,162,255,.55); background:rgba(122,162,255,.06); }
    .priority-lane.drop-cross { border-color:rgba(255,176,77,.55); background:rgba(255,176,77,.055); }
    .priority-lane-head { display:flex; align-items:center; gap:12px; color:#d0d6e0; font-size:12px; }
    .priority-lane-head strong { font-size:15px; color:#f7f8f8; letter-spacing:-.18px; }
    .priority-lane.drop-same .priority-lane-head::after { content:'松手：同优先级内排序，不变色'; color:#a9c3ff; margin-left:auto; font-size:12px; }
    .priority-lane.drop-cross .priority-lane-head::after { content:'松手切换到该优先级，卡片会变色'; color:#ffd59a; margin-left:auto; font-size:12px; }
    .task-card { display:grid; grid-template-columns: 4px 52px minmax(260px,1fr) minmax(100px,.45fr) minmax(300px,360px); gap:16px; align-items:center; padding:20px; border:1px solid rgba(255,255,255,0.08); border-radius:18px; background:rgba(255,255,255,0.035); transition:transform .16s ease, border-color .16s ease, background .16s ease; }
    .task-card.dragging { opacity:.55; transform:scale(.995); }
    .priority-card-P0 { background:linear-gradient(135deg, rgba(255,92,92,.11), rgba(255,255,255,.028)); border-color:rgba(255,112,112,.28); }
    .priority-card-P1 { background:linear-gradient(135deg, rgba(255,176,77,.10), rgba(255,255,255,.028)); border-color:rgba(255,176,77,.26); }
    .priority-card-P2 { background:linear-gradient(135deg, rgba(122,162,255,.10), rgba(255,255,255,.028)); border-color:rgba(122,162,255,.23); }
    .priority-card-P3 { background:linear-gradient(135deg, rgba(148,163,184,.075), rgba(255,255,255,.024)); border-color:rgba(148,163,184,.18); }
    .global-glass-surface, .item, .meta-pill, .kpi, .plane-view-toolbar, .priority-lane, .task-card, .insight, .deadline-body, .calendar-day, .task-modal, .field, select, textarea, input:not([type="checkbox"]) { background:linear-gradient(145deg, var(--glass-bg), rgba(255,255,255,.030)); border-color:var(--glass-border); box-shadow:inset 0 1px 0 rgba(255,255,255,.085), 0 18px 48px rgba(0,0,0,.34); backdrop-filter:blur(20px) saturate(112%); -webkit-backdrop-filter:blur(20px) saturate(112%); }
    .task-card.priority-card-P0 { --priority-color:#ff705d; background:linear-gradient(135deg, color-mix(in srgb, var(--priority-color), transparent 90%), rgba(255,255,255,.028)); border-color:color-mix(in srgb, var(--priority-color), rgba(255,255,255,.12) 58%); }
    .task-card.priority-card-P1 { --priority-color:#f0b84d; background:linear-gradient(135deg, color-mix(in srgb, var(--priority-color), transparent 92%), rgba(255,255,255,.026)); border-color:color-mix(in srgb, var(--priority-color), rgba(255,255,255,.12) 58%); }
    .task-card.priority-card-P2 { --priority-color:#7aa2ff; background:linear-gradient(135deg, color-mix(in srgb, var(--priority-color), transparent 92%), rgba(255,255,255,.026)); border-color:color-mix(in srgb, var(--priority-color), rgba(255,255,255,.12) 58%); }
    .task-card.priority-card-P3 { --priority-color:#94a3b8; background:linear-gradient(135deg, color-mix(in srgb, var(--priority-color), transparent 94%), rgba(255,255,255,.024)); border-color:color-mix(in srgb, var(--priority-color), rgba(255,255,255,.12) 58%); }
    .priority-accent { width:4px; align-self:stretch; border-radius:999px; background:var(--priority-color, #7aa2ff); opacity:.9; }
    .task-card.done { opacity:.58; }
    .task-drag-handle { display:inline-flex; align-items:center; gap:6px; width:max-content; color:#8a8f98; font-size:12px; margin-bottom:8px; cursor:grab; user-select:none; }
    .task-drag-handle:active { cursor:grabbing; }
    .task-drag-zone { min-height:100%; width:100%; justify-self:stretch; display:grid; place-content:center; cursor:grab; user-select:none; }
    .task-drag-zone:active { cursor:grabbing; }
    .grip-lines { display:grid; gap:7px; width:54px; opacity:.75; }
    .grip-line { height:3px; border-radius:999px; background:rgba(203,213,225,.62); box-shadow:0 0 10px rgba(203,213,225,.08); }
    .task-edit-cell { display:grid; place-items:start center; align-self:stretch; padding-top:2px; }
    .task-title-row { display:flex; align-items:center; gap:10px; margin-bottom:9px; }
    .work-item-key { font-family:'JetBrains Mono', ui-monospace, monospace; font-size:12px; color:#8a8f98; border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.03); border-radius:8px; padding:4px 7px; white-space:nowrap; }
    .task-main h4 { margin:0; color:#f7f8f8; font-size:23px; line-height:1.2; letter-spacing:-.32px; font-weight:650; }
    .task-main p { margin:0; color:#a9c3ff; line-height:1.55; font-size:15px; max-width:68ch; }
    .task-meta { display:flex; gap:8px; flex-wrap:wrap; margin-top:16px; }
    .property-pill, .task-category { font-size:12px; padding:7px 11px; color:#dfe7ff; background:rgba(255,255,255,.055); }
    .task-progress { display:grid; gap:14px; justify-items:stretch; }
    .progress-head { display:flex; align-items:center; justify-content:space-between; gap:12px; color:#d0d6e0; font-size:15px; }
    .progress-value { font-family:'JetBrains Mono', ui-monospace, monospace; color:#f7f8f8; }
    .progress-track { height:6px; border-radius:999px; background:linear-gradient(90deg, #7aa2ff var(--progress, 0%), rgba(255,255,255,.18) var(--progress, 0%)); box-shadow:inset 0 0 0 1px rgba(255,255,255,.04); }
    .progress-slider { width:100%; height:34px; accent-color:#7aa2ff; cursor:pointer; background:linear-gradient(90deg, #7aa2ff var(--progress, 0%), rgba(255,255,255,.18) var(--progress, 0%)); border-radius:999px; }
    .progress-slider::-webkit-slider-thumb { width:30px; height:30px; }
    .progress-slider::-moz-range-thumb { width:30px; height:30px; border-radius:50%; }
    .progress-scale { display:flex; justify-content:space-between; color:#62666d; font-size:12px; font-family:'JetBrains Mono', ui-monospace, monospace; }
    .edit-task-btn { width:46px; height:46px; border-radius:999px; border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.055); color:#dfe7ff; cursor:pointer; font-size:18px; }
    .badge { display:inline-flex; align-items:center; gap:5px; border-radius:999px; padding:5px 8px; font-size:11px; color:#d0d6e0; border:1px solid rgba(255,255,255,0.08); background:rgba(255,255,255,0.035); }
    .p-P0 { color:#ffb4b4; border-color:rgba(255,125,125,.35); }
    .p-P1 { color:#ffd59a; border-color:rgba(255,169,64,.35); }
    .p-P2 { color:#a9c3ff; border-color:rgba(122,162,255,.35); }
    .p-P3 { color:#b9c1d6; }
    .add-task-trigger { width:100%; margin-top:14px; padding:16px; border-radius:16px; border:1px dashed rgba(122,162,255,.45); background:rgba(122,162,255,.08); color:#dfe7ff; font-weight:600; cursor:pointer; }
    .task-modal-backdrop { position:fixed; inset:0; z-index:20; display:none; place-items:center; padding:24px; background:rgba(0,0,0,.62); backdrop-filter: blur(10px); }
    .task-modal-backdrop.open { display:grid; }
    .task-modal { width:min(980px, 100%); border:1px solid rgba(255,255,255,.12); border-radius:28px; background:#0f1728; box-shadow:0 24px 90px rgba(0,0,0,.45); padding:30px; }
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
      .span-8, .span-6, .span-4, .span-7, .span-5 { grid-column: span 12; }
      .kpis { grid-template-columns: repeat(2, minmax(0,1fr)); }
      .workbench-hero, .form-grid, .review-grid { grid-template-columns: 1fr; }
      .form-grid .wide { grid-column: span 1; }
      .review-grid .wide { grid-column: span 1; }
      .mini-stat { grid-template-columns:1fr; }
      .overview-dashboard { grid-template-columns:1fr; min-height:auto; }
      .overview-left-stage { min-height:auto; }
      .overview-greeting { position:relative; left:auto; top:auto; margin:0 0 18px; font-size:34px; }
      .overview-left-stage .orbit-map-stage { transform:none; }
      .overview-side-panel { grid-template-columns:1fr; grid-template-rows:auto; min-height:auto; }
      .side-card-wide { grid-column:span 1; }
      .orbit-core { width:124px; height:124px; }
      .orbit-task-card { position:relative; left:auto; top:auto; transform:none; width:auto; margin:10px; animation:none; }
      .orbit-connector-layer { display:none; }
      .overview-orbit-map { min-height:auto; height:auto; padding:150px 8px 18px; display:grid; gap:10px; }
      .overview-first-fold { min-height:auto; }
      .deadline-calendar { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .task-modal { max-height:88vh; overflow:auto; }
      .task-card { grid-template-columns:1fr; }
      .task-actions { justify-content:flex-start; }
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
    const priorityLabels = { P0: 'P0 火烧屁股', P1: 'P1 今日必完成', P2: 'P2 常规推进', P3: 'P3 可延后' };
    const priorityStyles = { P0: '#c95d45', P1: '#c49a42', P2: '#4f6f92', P3: '#4f9b72' };
    const categoryColors = { '生活': '#6ee7b7', '工作': '#7aa2ff', '自媒体': '#f472b6' };
    const urgencyRadius = { P0: 220, P1: 270, P2: 325, P3: 370 };
    const overviewEncouragements = ['开启美好的一天吧！', '今天也向前推进一点点。', '保持节奏，把重要的事做漂亮。', '灵感在线，稳稳推进。'];
    const taskCategories = ['生活', '工作', '自媒体'];
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

    function inferTaskCategory(task) {
      if (taskCategories.includes(task.category)) return task.category;
      const text = `${task.project || ''} ${task.title || ''} ${task.description || ''}`;
      if (/自媒体|内容|视频|小红书|公众号|抖音|B站/i.test(text)) return '自媒体';
      if (/生活|复盘|健康|家庭|个人/i.test(text)) return '生活';
      return '工作';
    }
    function normalizeTask(task, index) {
      const progress = Number.isFinite(Number(task.progress)) ? Math.min(100, Math.max(0, Number(task.progress))) : (task.status === 'done' ? 100 : 0);
      return {
        ...task,
        order: Number.isFinite(Number(task.order)) ? Number(task.order) : index,
        priority: task.priority || 'P3',
        category: inferTaskCategory(task),
        progress,
        status: progress >= 100 ? 'done' : (task.status === 'done' ? 'doing' : (task.status || 'todo')),
      };
    }
    function loadTasks() {
      const saved = readLocal(WORKBENCH_TASK_KEY, null);
      if (saved && Array.isArray(saved)) return saved.map(normalizeTask);
      const defaults = [
        { id: uid(), title: '确认个人工作台首页结构', description: '根据实际使用反馈调整今日总览、待办和复盘区块。', priority: 'P1', status: 'doing', progress: 45, order: 1, category: '工作', due: today?.date || '', focus: true, createdAt: new Date().toISOString() },
        { id: uid(), title: '晚上填写今日复盘', description: '记录完成事项、推进情况、阻塞和明日下一步。', priority: 'P0', status: 'todo', progress: 15, order: 0, category: '生活', due: today?.date || '', focus: true, createdAt: new Date().toISOString() },
        { id: uid(), title: '观察 19:30 飞书自动推送', description: '时间管理自动化保持不动，只观察是否稳定到达。', priority: 'P2', status: 'waiting', progress: 20, order: 2, category: '工作', due: today?.date || '', focus: false, createdAt: new Date().toISOString() },
      ].map(normalizeTask);
      writeLocal(WORKBENCH_TASK_KEY, defaults);
      return defaults;
    }
    function saveTasks(tasks) { writeLocal(WORKBENCH_TASK_KEY, tasks.map(normalizeTask)); }
    function loadReviews() { return readLocal(WORKBENCH_REVIEW_KEY, {}); }
    function saveReviews(reviews) { writeLocal(WORKBENCH_REVIEW_KEY, reviews); }
    function sortTasksForDisplay(tasks) {
      return [...tasks].sort((a,b) => (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9) || (a.order ?? 999) - (b.order ?? 999) || String(a.createdAt || '').localeCompare(String(b.createdAt || '')));
    }
    function activeTasks() { return sortTasksForDisplay(loadTasks().filter(task => task.status !== 'done')); }
    function taskStats(tasks) {
      const pending = tasks.filter(t => t.status !== 'done');
      const done = tasks.filter(t => t.status === 'done').length;
      const counts = Object.fromEntries(Object.keys(statusStyles).map(key => [key, tasks.filter(t => t.status === key).length]));
      const priorityCounts = Object.fromEntries(Object.keys(priorityRank).map(key => [key, pending.filter(t => t.priority === key).length]));
      const high = pending.filter(t => ['P0','P1'].includes(t.priority)).length;
      return { total: tasks.length, pending: pending.length, done, counts, priorityCounts, high, blocked: tasks.filter(t => t.status === 'blocked').length };
    }
    function priorityDonutStyle(stats) {
      const total = Math.max(stats.pending, 1);
      let cursor = 0;
      const segments = ['P0', 'P1', 'P2', 'P3'].map(key => {
        const count = stats.priorityCounts[key] || 0;
        const start = cursor;
        cursor += (count / total) * 100;
        return `${priorityStyles[key]} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
      });
      if (stats.pending === 0) return 'background: conic-gradient(#63627f 0% 100%);';
      return `background: conic-gradient(${segments.join(', ')});`;
    }
    function renderPriorityLegend(stats) {
      return ['P0', 'P1', 'P2', 'P3'].map(key => `
        <span class="legend-pill"><i class="dot" style="background:${priorityStyles[key]}"></i>${priorityLabels[key]} ${stats.priorityCounts[key] || 0}</span>
      `).join('');
    }

    let editingTaskId = null;
    window.addTask = function addTask() {
      const titleInput = document.getElementById('taskTitle');
      const title = titleInput.value.trim();
      if (!title) return;
      const tasks = loadTasks();
      const priority = document.getElementById('taskPriority').value;
      const existing = editingTaskId ? tasks.find(task => task.id === editingTaskId) : null;
      const nextOrder = Math.max(-1, ...tasks.filter(task => task.priority === priority && task.id !== editingTaskId).map(task => Number(task.order) || 0)) + 1;
      const payload = {
        title,
        description: document.getElementById('taskDescription').value.trim(),
        priority,
        order: existing?.priority === priority ? existing.order : nextOrder,
        category: document.getElementById('taskCategory').value,
        due: document.getElementById('taskDue').value,
        updatedAt: new Date().toISOString(),
      };
      const next = existing
        ? tasks.map(task => task.id === editingTaskId ? {...task, ...payload} : task)
        : [...tasks, { id: uid(), ...payload, status: 'todo', progress: 0, createdAt: new Date().toISOString() }];
      saveTasks(next);
      closeTaskModal();
      renderWorkbench();
    };
    window.openTaskModal = function openTaskModal(id = null) {
      editingTaskId = id;
      const modal = document.getElementById('taskModal');
      const task = id ? loadTasks().find(item => item.id === id) : null;
      const title = document.getElementById('taskModalTitle');
      const intro = document.getElementById('taskModalIntro');
      const action = document.getElementById('taskModalAction');
      const deleteButton = document.getElementById('taskModalDelete');
      if (title) title.textContent = task ? '编辑待办' : '新增待办';
      if (intro) intro.textContent = task ? '调整标题、描述、优先级、截止日期或删除这张卡片。' : '这是一个强操作入口，填写完成后会回到待办中心。';
      if (action) action.textContent = task ? '保存修改' : '添加待办';
      if (deleteButton) deleteButton.style.display = task ? 'inline-flex' : 'none';
      const setValue = (field, value) => { const node = document.getElementById(field); if (node) node.value = value || ''; };
      setValue('taskTitle', task?.title || '');
      setValue('taskPriority', task?.priority || 'P1');
      setValue('taskCategory', task?.category || '工作');
      setValue('taskDue', task?.due || today?.date || '');
      setValue('taskDescription', task?.description || '');
      if (modal) modal.classList.add('open');
    };
    window.closeTaskModal = function closeTaskModal() {
      const modal = document.getElementById('taskModal');
      if (modal) modal.classList.remove('open');
      editingTaskId = null;
    };
    window.updateTaskStatus = function updateTaskStatus(id, status) {
      const tasks = loadTasks().map(task => task.id === id ? {...task, status, updatedAt: new Date().toISOString()} : task);
      saveTasks(tasks); renderWorkbench();
    };
    window.previewTaskProgress = function previewTaskProgress(input) {
      const value = Math.min(100, Math.max(0, Number(input.value) || 0));
      const panel = input.closest('.task-progress');
      const label = panel?.querySelector('.progress-value');
      if (label) label.textContent = `${value}%`;
      input.style.setProperty('--progress', `${value}%`);
      panel?.querySelector('.progress-track')?.style.setProperty('--progress', `${value}%`);
    };
    window.commitTaskProgress = function commitTaskProgress(id, progress) {
      const value = Math.min(100, Math.max(0, Number(progress) || 0));
      const tasks = loadTasks().map(task => task.id === id ? {
        ...task,
        progress: value,
        status: value >= 100 ? 'done' : (task.status === 'done' ? 'doing' : task.status),
        updatedAt: new Date().toISOString(),
      } : task);
      saveTasks(tasks); renderWorkbench();
    };
    window.updateTaskProgress = window.commitTaskProgress;
    let dragTaskId = null;
    let dragSourcePriority = null;
    function clearPriorityDropHints() {
      document.querySelectorAll('.priority-lane').forEach(lane => lane.classList.remove('drop-same', 'drop-cross'));
    }
    window.beginTaskDrag = function beginTaskDrag(id, priority, event) {
      dragTaskId = id;
      dragSourcePriority = priority;
      const card = event.currentTarget.closest('.task-card');
      if (card) card.classList.add('dragging');
      if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
    };
    window.endTaskDrag = function endTaskDrag(event) {
      const card = event.currentTarget.closest('.task-card');
      if (card) card.classList.remove('dragging');
      clearPriorityDropHints();
    };
    window.markPriorityDrop = function markPriorityDrop(priority, event) {
      event.preventDefault();
      clearPriorityDropHints();
      const lane = event.currentTarget.closest('.priority-lane');
      if (lane) lane.classList.add(priority === dragSourcePriority ? 'drop-same' : 'drop-cross');
    };
    function applyPriorityGroupOrder(allTasks, priority, orderedIds) {
      let next = 0;
      const idOrder = Object.fromEntries(orderedIds.map((id, index) => [id, index]));
      return allTasks.map(task => task.priority === priority && idOrder[task.id] !== undefined
        ? {...task, order: idOrder[task.id], updatedAt: new Date().toISOString()}
        : task
      ).map(task => task.priority === priority && idOrder[task.id] === undefined
        ? {...task, order: orderedIds.length + next++, updatedAt: new Date().toISOString()}
        : task
      );
    }
    window.reorderTasks = function reorderTasks(targetId, targetPriority, event) {
      if (event) { event.preventDefault(); event.stopPropagation(); }
      if (!dragTaskId || dragTaskId === targetId) return;
      const allTasks = loadTasks();
      const dragged = allTasks.find(task => task.id === dragTaskId);
      if (!dragged) return;
      const destinationPriority = targetPriority || dragged.priority;
      let group = sortTasksForDisplay(allTasks.filter(task => task.status !== 'done' && task.priority === destinationPriority && task.id !== dragTaskId));
      const targetIndex = Math.max(0, group.findIndex(task => task.id === targetId));
      const moved = {...dragged, priority: destinationPriority, updatedAt: new Date().toISOString()};
      group.splice(targetIndex, 0, moved);
      const groupIds = group.map(task => task.id);
      const movedById = Object.fromEntries(group.map((task, index) => [task.id, {...task, order: index}]));
      let next = allTasks.map(task => movedById[task.id] || (task.id === dragTaskId ? movedById[dragTaskId] : task));
      next = applyPriorityGroupOrder(next, destinationPriority, groupIds);
      saveTasks(next);
      dragTaskId = null;
      dragSourcePriority = null;
      clearPriorityDropHints();
      renderWorkbench();
    };
    window.dropPriorityLane = function dropPriorityLane(priority, event) {
      event.preventDefault();
      if (!dragTaskId) return;
      const allTasks = loadTasks();
      const dragged = allTasks.find(task => task.id === dragTaskId);
      if (!dragged) return;
      const group = sortTasksForDisplay(allTasks.filter(task => task.status !== 'done' && task.priority === priority && task.id !== dragTaskId));
      group.push({...dragged, priority, updatedAt: new Date().toISOString()});
      const movedById = Object.fromEntries(group.map((task, index) => [task.id, {...task, order: index}]));
      saveTasks(allTasks.map(task => movedById[task.id] || task));
      dragTaskId = null;
      dragSourcePriority = null;
      clearPriorityDropHints();
      renderWorkbench();
    };
    window.deleteEditingTask = function deleteEditingTask() {
      if (!editingTaskId) return;
      if (deleteTask(editingTaskId)) closeTaskModal();
    };
    window.deleteTask = function deleteTask(id) {
      if (!confirm('确认删除这条待办？')) return false;
      saveTasks(loadTasks().filter(task => task.id !== id));
      renderWorkbench();
      return true;
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
    let taskCategoryFilter = localStorage.getItem('taskCategoryFilter') || 'all';

    window.setTaskCategoryFilter = function setTaskCategoryFilter(category) {
      taskCategoryFilter = category === 'all' || taskCategories.includes(category) ? category : 'all';
      localStorage.setItem('taskCategoryFilter', taskCategoryFilter);
      renderWorkbench();
    };

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

    function renderTaskCard(task, index) {
      const workItemKey = `PWB-${String(index + 1).padStart(3, '0')}`;
      const progress = Math.min(100, Math.max(0, Number(task.progress) || 0));
      return `
        <div class="task-card priority-card-${task.priority} ${task.status === 'done' ? 'done' : ''}" style="--priority-color:${priorityStyles[task.priority] || '#7aa2ff'}" ondragover="markPriorityDrop('${task.priority}', event)" ondrop="reorderTasks('${task.id}', '${task.priority}', event)">
          <div class="priority-accent" aria-hidden="true"></div>
          <div class="task-edit-cell">
            <button class="edit-task-btn" onclick="openTaskModal('${task.id}')" aria-label="编辑 ${escapeHtml(task.title)}">✎</button>
          </div>
          <div class="task-main">
            <div class="task-title-row"><span class="work-item-key">${workItemKey}</span><h4>${escapeHtml(task.title)}</h4></div>
            <p>${escapeHtml(task.description || '暂无描述')}</p>
            <div class="task-meta">
              <span class="badge property-pill task-category">${escapeHtml(task.category || '工作')}</span>
              ${task.due ? `<span class="badge property-pill">截止 ${escapeHtml(task.due)}</span>` : ''}
            </div>
          </div>
          <div class="task-drag-zone task-drag-handle" draggable="true" ondragstart="beginTaskDrag('${task.id}', '${task.priority}', event)" ondragend="endTaskDrag(event)" aria-label="拖动调整任务顺序或优先级">
            <div class="grip-lines" aria-hidden="true"><span class="grip-line"></span><span class="grip-line"></span><span class="grip-line"></span></div>
          </div>
          <div class="task-progress">
            <div class="progress-head"><span>推进程度</span><strong class="progress-value">${progress}%</strong></div>
            <div class="progress-track" style="--progress:${task.progress || 0}%"></div>
            <input class="progress-slider" type="range" min="0" max="100" step="1" value="${progress}" style="--progress:${task.progress || 0}%" oninput="previewTaskProgress(this)" onchange="commitTaskProgress('${task.id}', this.value)" aria-label="${escapeHtml(task.title)} 推进程度" />
            <div class="progress-scale"><span>0%</span><span>100%</span></div>
          </div>
        </div>`;
    }

    function renderTasks(tasks) {
      const sorted = sortTasksForDisplay(tasks);
      const visibleTasks = taskCategoryFilter === 'all' ? sorted : sorted.filter(task => (task.category || '工作') === taskCategoryFilter);
      const priorities = ['P0', 'P1', 'P2', 'P3'];
      const categoryButtons = taskCategories.map(category => `<button type="button" class="view-chip task-category-filter ${taskCategoryFilter === category ? 'active' : ''}" onclick="setTaskCategoryFilter('${category}')" aria-pressed="${taskCategoryFilter === category}">${category}</button>`).join('');
      const toolbar = `<div class="plane-view-toolbar">
        <div class="view-title"><span class="view-icon">▦</span><span>工作项视图</span><small>${visibleTasks.length} 个待推进项</small></div>
        <div class="view-chips">
          <button type="button" class="view-chip ${taskCategoryFilter === 'all' ? 'active' : ''}" onclick="setTaskCategoryFilter('all')" aria-pressed="${taskCategoryFilter === 'all'}">按优先级</button>
          ${categoryButtons}
        </div>
      </div>`;
      if (!sorted.length) return `${toolbar}<div class="empty">暂无待办。先添加今天要推进的事项。</div>`;
      if (!visibleTasks.length) return `${toolbar}<div class="empty">当前分类暂无待办。可以切回“按优先级”查看全部任务。</div>`;
      const sequenceById = new Map(visibleTasks.map((task, index) => [task.id, index]));
      return `${toolbar}<div class="task-list">${priorities.map(priority => {
        const group = visibleTasks.filter(task => task.priority === priority);
        return `<section class="priority-lane priority-card-${priority}" ondragover="markPriorityDrop('${priority}', event)" ondrop="dropPriorityLane('${priority}', event)">
          <div class="priority-lane-head">
            <strong>${priorityLabels[priority]} · ${group.length} 项</strong>
          </div>
          ${group.length ? group.map(task => renderTaskCard(task, sequenceById.get(task.id) || 0)).join('') : '<div class="empty">拖到这里可切换为该优先级。</div>'}
        </section>`;
      }).join('')}</div>`;
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
            <span class="badge p-${task.priority}">${priorityLabels[task.priority]}</span>
            <span class="badge">${escapeHtml(task.category || '工作')}</span>
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

    function orbitPosition(task, index, total) {
      const radius = urgencyRadius[task.priority] || 300;
      const baseAngles = [-150, -28, 150, 28, -96, 96, -178, 2, -62, 62];
      const angleDegrees = baseAngles[index] ?? ((index * 137.5) % 360 - 180);
      const angle = angleDegrees * Math.PI / 180;
      return {
        x: Math.round(Math.cos(angle) * radius * 0.62),
        y: Math.round(Math.sin(angle) * radius * 0.68),
        angle: angleDegrees,
        radius,
      };
    }

    function orbitConnectorPath(x, y, index) {
      const node = { orbitX:x, orbitY:y, orbitIndex:index };
      return wavyConnectorPath(node, connectorEndpoint(node));
    }

    function layoutOrbitOverview() {
      document.querySelectorAll('.overview-left-stage .orbit-map-stage').forEach(stage => {
        const box = stage.getBoundingClientRect();
        if (!box.width || !box.height) return;
        const spreadX = Math.max(240, (box.width - 320) / 2);
        const spreadY = Math.max(190, (box.height - 260) / 2);
        stage.style.setProperty('--orbit-spread-x', `${Math.round(spreadX)}px`);
        stage.style.setProperty('--orbit-spread-y', `${Math.round(spreadY)}px`);
        stage.querySelectorAll('.orbit-task-card').forEach((card, index) => {
          const angle = Number(card.dataset.orbitAngle || 0) * Math.PI / 180;
          const radius = Number(card.dataset.orbitRadius || 300);
          const ratio = Math.min(1, Math.max(.52, radius / 370));
          const x = Math.round(Math.cos(angle) * spreadX * ratio);
          const y = Math.round(Math.sin(angle) * spreadY * ratio);
          card.style.setProperty('--orbit-x', `${x}px`);
          card.style.setProperty('--orbit-y', `${y}px`);
          const connector = stage.querySelector(`.orbit-connector[data-connector-index="${index}"]`);
          if (connector) {
            const path = orbitConnectorPath(x, y, index);
            connector.querySelectorAll('path').forEach(element => element.setAttribute('d', path));
          }
        });
      });
    }

    window.addEventListener('resize', layoutOrbitOverview);

    function connectorEndpoint(node) {
      const cardHalfWidth = 110;
      const cardHalfHeight = 54;
      const distance = Math.max(Math.hypot(node.orbitX, node.orbitY), 1);
      const unitX = node.orbitX / distance;
      const unitY = node.orbitY / distance;
      const xTrim = Math.abs(unitX) < 0.001 ? Infinity : cardHalfWidth / Math.abs(unitX);
      const yTrim = Math.abs(unitY) < 0.001 ? Infinity : cardHalfHeight / Math.abs(unitY);
      const trimToCardEdge = Math.min(xTrim, yTrim);
      const length = Math.max(18, distance - trimToCardEdge);
      return {
        x: Math.round(unitX * length),
        y: Math.round(unitY * length),
        length: Math.round(length),
        angle: Math.round(Math.atan2(node.orbitY, node.orbitX) * 1800 / Math.PI) / 10,
      };
    }

    function wavyConnectorPath(node, endpoint) {
      const normalLength = Math.max(Math.hypot(endpoint.x, endpoint.y), 1);
      const normalX = -endpoint.y / normalLength;
      const normalY = endpoint.x / normalLength;
      const wave = (node.orbitIndex % 2 === 0 ? 1 : -1) * Math.min(58, Math.max(24, normalLength * 0.14));
      const controlA = {
        x: Math.round(endpoint.x * 0.30 + normalX * wave),
        y: Math.round(endpoint.y * 0.30 + normalY * wave),
      };
      const controlB = {
        x: Math.round(endpoint.x * 0.68 - normalX * wave * 0.72),
        y: Math.round(endpoint.y * 0.68 - normalY * wave * 0.72),
      };
      return `M 0 0 C ${controlA.x} ${controlA.y}, ${controlB.x} ${controlB.y}, ${endpoint.x} ${endpoint.y}`;
    }

    function overviewGreeting() {
      const index = Math.floor(Math.random() * overviewEncouragements.length);
      return `令辉，${overviewEncouragements[index]}`;
    }

    function previousDayRecord() {
      if (!today) return days[1] || null;
      return days.find(day => day.date < today.date) || days[1] || null;
    }

    function renderOverviewCalendar() {
      const base = today?.date || new Date().toISOString().slice(0, 10);
      const [year, month, activeDay] = base.split('-').map(Number);
      const monthStart = new Date(Date.UTC(year, month - 1, 1));
      const startOffset = (monthStart.getUTCDay() + 6) % 7;
      const gridStart = new Date(Date.UTC(year, month - 1, 1 - startOffset));
      const cells = Array.from({length: 35}, (_, index) => {
        const date = new Date(Date.UTC(gridStart.getUTCFullYear(), gridStart.getUTCMonth(), gridStart.getUTCDate() + index));
        const dateStr = `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`;
        const inMonth = date.getUTCMonth() === month - 1;
        const classes = dateStr === base ? 'calendar-day-cell active' : `calendar-day-cell${inMonth ? '' : ' muted'}`;
        return `<span class="${classes}">${date.getUTCDate()}</span>`;
      }).join('');
      return `<section class="glass-card calendar-card side-card-wide">
        <div class="calendar-head">
          <span class="calendar-nav" aria-hidden="true">‹</span>
          <h4 class="calendar-month-title">${year}年${month}月</h4>
          <span class="calendar-nav" aria-hidden="true">›</span>
        </div>
        <div class="calendar-weekdays"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div>
        <div class="calendar-month-grid">${cells}</div>
      </section>`;
    }

    function renderYesterdayLineChart(day) {
      const values = Array.from({length: 24}, (_, hour) => Number(day?.hourly_seconds?.[String(hour)] || 0));
      const max = Math.max(...values, 1);
      const points = values.map((value, index) => `${Math.round((index / 23) * 240)},${Math.round(82 - (value / max) * 68)}`).join(' ');
      const fillPoints = `0,92 ${points} 240,92`;
      return `<section class="glass-card time-line-card">
        <h4>昨日电脑使用</h4>
        <div class="glass-big-number">${day ? fmtDuration(day.total_active_seconds) : '暂无数据'}</div>
        <svg class="line-chart-svg" viewBox="0 0 240 96" preserveAspectRatio="none" aria-hidden="true">
          <line class="line-chart-axis" x1="0" y1="92" x2="240" y2="92" />
          <polygon class="line-chart-fill" points="${fillPoints}" />
          <polyline class="line-chart-path" points="${points}" />
        </svg>
      </section>`;
    }

    function renderYesterdayCategoryDonut(day) {
      const categories = (day?.categories || []).slice(0, 4);
      let cursor = 0;
      const segments = categories.length ? categories.map(cat => {
        const start = cursor;
        cursor += (Number(cat.share) || 0) * 100;
        return `${cat.color || '#7aa2ff'} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
      }) : ['rgba(255,255,255,.14) 0% 100%'];
      return `<section class="glass-card category-donut-card">
        <h4>昨日时间比例</h4>
        <div class="side-donut-wrap">
          <div class="side-donut" style="background:conic-gradient(${segments.join(', ')});"></div>
          <div class="side-legend">${(categories.length ? categories : [{name:'暂无数据', share:1, color:'#8a8f98'}]).map(cat => `
            <div class="side-legend-row"><span><i style="background:${cat.color || '#8a8f98'}"></i>${escapeHtml(cat.name)}</span><b>${pct(Number(cat.share) || 0)}</b></div>
          `).join('')}</div>
        </div>
      </section>`;
    }

    function renderYesterdayCarryover(tasks, day) {
      const pending = sortTasksForDisplay(tasks.filter(task => task.status !== 'done'));
      const carryover = pending.filter(task => day?.date && task.due && task.due <= day.date);
      const items = (carryover.length ? carryover : pending).slice(0, 3);
      return `<section class="glass-card carryover-card side-card-wide">
        <h4>昨日遗留任务</h4>
        <div class="glass-subtitle">优先处理这些还没有收口的事项</div>
        <div class="carryover-list">${items.length ? items.map(task => `
          <div class="carryover-item"><strong>${escapeHtml(task.title)}</strong><small>${priorityLabels[task.priority] || task.priority} · ${escapeHtml(task.category || '工作')} · ${task.due ? `截止 ${escapeHtml(task.due)}` : '无截止'}</small></div>
        `).join('') : '<div class="empty">暂无昨日遗留任务。</div>'}</div>
      </section>`;
    }

    function renderOverviewSidePanel(tasks) {
      const yesterday = previousDayRecord();
      return `<aside class="overview-side-panel">
        ${renderOverviewCalendar()}
        ${renderYesterdayLineChart(yesterday)}
        ${renderYesterdayCategoryDonut(yesterday)}
        ${renderYesterdayCarryover(tasks, yesterday)}
      </aside>`;
    }

    function renderOrbitOverview(tasks, stats) {
      const pending = sortTasksForDisplay(tasks.filter(task => task.status !== 'done')).slice(0, 10);
      const nodes = pending.map((task, index) => {
        const position = orbitPosition(task, index, pending.length);
        const category = task.category || '工作';
        const color = categoryColors[category] || categoryColors['工作'];
        return {...task, orbitIndex: index, orbitX: position.x, orbitY: position.y, orbitAngle: position.angle, orbitRadius: position.radius, orbitColor: color, orbitCategory: category};
      });
      const connectors = nodes.map(task => {
        const endpoint = connectorEndpoint(task);
        const path = wavyConnectorPath(task, endpoint);
        return `
          <g class="orbit-connector" data-connector-index="${task.orbitIndex}" style="--cat-color:${task.orbitColor}; --delay:${task.orbitIndex * 90}ms">
            <path class="orbit-connector-path" d="${path}" />
            <path class="orbit-connector-particles" d="${path}" />
          </g>`;
      }).join('');
      const cards = nodes.map(task => `
        <article class="orbit-task-card liquid-glass" data-orbit-angle="${task.orbitAngle}" data-orbit-radius="${task.orbitRadius}" style="--orbit-x:${task.orbitX}px; --orbit-y:${task.orbitY}px; --cat-color:${task.orbitColor}; --delay:${task.orbitIndex * 90}ms">
          <div class="orbit-task-head"><span class="orbit-category-dot"></span><small>${task.priority}</small></div>
          <b>${escapeHtml(task.title)}</b>
          <div class="orbit-task-meta"><span>${escapeHtml(task.orbitCategory)}</span><span>${task.due ? `截止 ${escapeHtml(task.due)}` : '无截止'}</span></div>
        </article>
      `).join('');
      const legend = taskCategories.map(category => {
        const count = pending.filter(task => (task.category || '工作') === category).length;
        return `<span class="legend-pill"><i class="dot" style="background:${categoryColors[category]}"></i>${category} ${count}</span>`;
      }).join('');
      return `
        <div class="overview-dashboard">
          <div class="overview-left-stage">
            <h2 class="overview-greeting">${overviewGreeting()}</h2>
            <div class="overview-orbit-map">
              <div class="orbit-map-stage">
                <svg class="orbit-connector-layer" aria-hidden="true">${connectors}</svg>
                <div class="orbit-core liquid-glass"><div class="orbit-core-inner"><div class="orbit-count">${stats.pending}</div></div></div>
                ${cards || '<div class="orbit-empty">暂无待推进事项</div>'}
                <div class="orbit-legend">${legend}</div>
              </div>
            </div>
          </div>
          ${renderOverviewSidePanel(tasks)}
        </div>`;
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
          ${renderOrbitOverview(tasks, stats)}
        </article>
        <article class="card span-12 next-fold"><h3 class="section-title">待办中心</h3>${renderTasks(tasks)}
          <button class="add-task-trigger" onclick="openTaskModal()">＋ 新增待办</button>
        </article>
        <div class="task-modal-backdrop" id="taskModal" onclick="if (event.target === this) closeTaskModal()">
          <div class="task-modal" role="dialog" aria-modal="true" aria-labelledby="taskModalTitle">
            <div class="task-modal-head">
              <div><h3 id="taskModalTitle">新增待办</h3><p id="taskModalIntro">这是一个强操作入口，填写完成后会回到待办中心。</p></div>
              <button class="icon-close" onclick="closeTaskModal()" aria-label="关闭">×</button>
            </div>
            <div class="form-grid">
              <label>标题<input id="taskTitle" placeholder="例如：完成首页布局" /></label>
              <label>优先级<select id="taskPriority"><option>P0</option><option selected>P1</option><option>P2</option><option>P3</option></select></label>
              <label>分类<select id="taskCategory"><option>生活</option><option selected>工作</option><option>自媒体</option></select></label>
              <label>截止<input id="taskDue" type="date" value="${today?.date || ''}" /></label>
              <label class="wide">描述<input id="taskDescription" placeholder="补充下一步动作、验收标准或背景" /></label>
              <div class="toolbar"><button class="btn primary" id="taskModalAction" onclick="addTask()">添加待办</button><button class="btn danger" id="taskModalDelete" onclick="deleteEditingTask()" style="display:none;">删除卡片</button><button class="btn" onclick="closeTaskModal()">取消</button></div>
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
      requestAnimationFrame(layoutOrbitOverview);
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
