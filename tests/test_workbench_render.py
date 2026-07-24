from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_dashboard.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sync_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample_history():
    return {
        "days": [
            {
                "date": "2026-07-01",
                "weekday": "周三",
                "total_active_seconds": 3600,
                "total_active_text": "1小时",
                "top_categories": ["设计", "学习/信息"],
                "categories": [
                    {"name": "设计", "seconds": 2400, "share": 0.6667, "color": "#f6bd16"},
                    {"name": "学习/信息", "seconds": 1200, "share": 0.3333, "color": "#61ddaa"},
                ],
                "switch_count": 12,
                "summary_line": "测试摘要",
                "notes": ["测试复盘提示"],
                "longest_focus": {"text": "20分", "range": "14:00–14:20", "activity": "Figma", "seconds": 1200},
                "energy": {"confidence": "中", "high_zone": "设计", "fragmented_zone": "学习/信息", "recovery_zone": "无", "evidence": ["证据"]},
                "hourly_seconds": {str(i): (600 if i == 14 else 0) for i in range(24)},
                "top_activities": [{"activity": "Figma", "text": "40分", "seconds": 2400}],
            }
        ],
        "weeks": [],
        "meta": {"generated_at": "2026-07-01T22:00:00+08:00", "delivery_time": "19:30", "db_path": "test.db"},
    }


def test_render_html_upgrades_time_dashboard_into_personal_workbench():
    module = load_module()
    html = module.render_html(sample_history())

    assert "个人工作台" in html
    assert "时间与精力" in html
    assert "待办中心" in html
    assert "今日复盘" in html
    assert "localStorage" in html
    assert "workbenchTasks" in html
    assert "workbenchReviews" in html
    assert "workbenchJsonMigratedAt" in html
    assert "WORKBENCH_SAVE_URL" in html
    assert '"workbench"' in html
    assert "renderWorkbench" in html
    assert "每日 19:30" in html


def test_render_html_contains_editable_task_and_review_controls():
    module = load_module()
    html = module.render_html(sample_history())

    assert "addTask" in html
    assert "saveReview" in html
    assert "priority" in html
    assert "openTaskModal('${task.id}')" in html
    assert "deleteEditingTask" in html


def test_workbench_data_uses_project_json_with_localstorage_migration_safety():
    module = load_module()
    workbench = {
        "tasks": [{"id": "json-task", "title": "JSON 任务", "priority": "P1", "category": "工作"}],
        "reviews": {"2026-07-01": {"done": "JSON 复盘"}},
        "ui": {"deadlineViewMode": "calendar", "taskCategoryFilter": "生活"},
        "meta": {"schema_version": 1, "source": "test"},
    }
    html = module.render_html(sample_history(), workbench)

    assert '"id": "json-task"' in html
    assert '"done": "JSON 复盘"' in html
    assert "const projectWorkbench = store.workbench" in html
    assert "function loadWorkbenchDraft()" in html
    assert "migrated_from_localStorage" in html
    assert "if (workbenchDraft.meta?.migrated_from_localStorage) queueWorkbenchPersist();" in html
    assert "fetch(WORKBENCH_SAVE_URL" in html
    assert "function saveTasks(tasks) { workbenchDraft.tasks = tasks.map(normalizeTask); queueWorkbenchPersist(); }" in html
    assert "function saveReviews(reviews) { workbenchDraft.reviews = reviews; queueWorkbenchPersist(); }" in html
    assert "localStorage.setItem(key" in html  # fallback backup only, not the source of truth


def test_workbench_json_is_staged_and_local_save_service_is_available():
    module = load_module()

    assert module.WORKBENCH_PATH.name == "workbench.json"
    normalized = module.normalize_workbench_data({"tasks": ["a"], "reviews": {"d": {}}, "ui": {"deadlineViewMode": "calendar"}})
    assert normalized["tasks"] == ["a"]
    assert normalized["reviews"] == {"d": {}}
    assert normalized["ui"]["deadlineViewMode"] == "calendar"
    assert module.allowed_workbench_origin("https://klinghui80-web.github.io")
    assert module.allowed_workbench_origin("http://127.0.0.1:8765")
    assert not module.allowed_workbench_origin("https://evil.example")

    source = SCRIPT.read_text(encoding="utf-8")
    assert '"data/workbench.json"' in source
    assert "--serve-workbench" in source
    assert "ThreadingHTTPServer((\"127.0.0.1\", port), WorkbenchHandler)" in source


def make_activitywatch_test_db(module):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE bucketmodel (key INTEGER PRIMARY KEY, id TEXT, type TEXT, created TEXT)")
    conn.execute("CREATE TABLE eventmodel (bucket_id INTEGER, timestamp TEXT, duration REAL, datastr TEXT)")
    conn.execute("INSERT INTO bucketmodel VALUES (1, 'aw-watcher-window_test', 'currentwindow', '2026-07-01T00:00:00')")
    conn.execute("INSERT INTO bucketmodel VALUES (2, 'aw-watcher-afk_test', 'afkstatus', '2026-07-01T00:00:00')")
    day = date(2026, 7, 1)

    def ts(hour, minute=0):
        return datetime.combine(day, datetime.min.time(), tzinfo=module.LOCAL_TZ).replace(hour=hour, minute=minute).isoformat(sep=" ")

    conn.execute(
        "INSERT INTO eventmodel VALUES (1, ?, ?, ?)",
        (ts(9), 3600, '{"app":"Figma","title":"设计稿"}'),
    )
    conn.execute("INSERT INTO eventmodel VALUES (2, ?, ?, ?)", (ts(9), 1200, '{"status":"not-afk"}'))
    conn.execute("INSERT INTO eventmodel VALUES (2, ?, ?, ?)", (ts(9, 20), 1800, '{"status":"afk"}'))
    conn.execute("INSERT INTO eventmodel VALUES (2, ?, ?, ?)", (ts(9, 50), 600, '{"status":"not-afk"}'))
    return conn, day


def test_activitywatch_afk_away_time_is_excluded_before_daily_totals_and_categories():
    module = load_module()
    conn, day = make_activitywatch_test_db(module)
    try:
        statuses = module.inspect_afk_statuses(conn)
        segments = module.load_segments(conn, day)
        summary = module.build_daily_summary(day, segments)
    finally:
        conn.close()

    assert statuses["bucket_found"] is True
    assert statuses["statuses"]["afk"] >= 1
    assert statuses["statuses"]["not-afk"] >= 1
    assert round(sum(seg.seconds for seg in segments)) == 1800
    assert summary["total_active_seconds"] == 1800
    assert summary["categories"][0]["seconds"] == 1800
    assert summary["data_policy"]["afk_excluded"] is True
    assert "已剔除 ActivityWatch AFK/离开时间" in summary["data_policy"]["ai_prompt_note"]


def test_low_usage_days_are_saved_as_low_confidence_but_not_eligible_for_automation_push():
    module = load_module()
    low_day = {
        "date": "2026-07-01",
        "weekday": "周三",
        "total_active_seconds": module.LOW_USAGE_THRESHOLD_SECONDS - 60,
        "total_active_text": "2小时59分",
        "top_categories": ["设计"],
        "categories": [{"name": "设计", "seconds": 1000, "share": 1, "color": "#f6bd16"}],
        "summary_line": "旧摘要",
        "notes": ["旧详细分析"],
        "energy": {"confidence": "高"},
        "longest_focus": {"text": "10分", "range": "09:00–09:10"},
    }
    normalized = module.apply_low_usage_policy(low_day.copy())
    history = {"days": [normalized], "weeks": []}
    state = {"sent_days": [], "sent_weeks": []}
    state_path = ROOT / "tests" / ".tmp_state_low_usage.json"
    if state_path.exists():
        state_path.unlink()
    original_state_path = module.STATE_PATH
    module.STATE_PATH = state_path
    now = datetime(2026, 7, 2, 20, 0, tzinfo=module.LOCAL_TZ)

    try:
        skipped = module.mark_low_usage_due_days(history, state, now)
    finally:
        module.STATE_PATH = original_state_path
        if state_path.exists():
            state_path.unlink()

    assert normalized["summary_line"] == "今日数据量不足"
    assert normalized["energy"]["confidence"] == "低"
    assert normalized["notes"] == ["今日使用时长未超过3小时，仅保存基础统计，不生成详细分析。"]
    assert normalized["data_policy"]["low_usage_no_ai_analysis"] is True
    assert skipped == ["2026-07-01"]
    assert "2026-07-01" in state["low_usage_days"]
    assert module.eligible_unsent_day(history, state, now) is None


def test_automation_message_mentions_afk_removed_for_days_that_are_pushed():
    module = load_module()
    day = sample_history()["days"][0]
    day["total_active_seconds"] = module.LOW_USAGE_THRESHOLD_SECONDS + 1
    day["data_policy"] = {"ai_prompt_note": "统计口径：已剔除 ActivityWatch AFK/离开时间。"}
    message = module.build_message(day, None, {"message": "已推送到 GitHub。"})

    assert "统计口径：已剔除 ActivityWatch AFK/离开时间。" in message


def test_first_screen_is_a_radial_task_orbit_overview_not_a_donut():
    module = load_module()
    html = module.render_html(sample_history())

    assert "overview-first-fold" in html
    assert "renderOrbitOverview" in html
    assert "overview-orbit-map" in html
    assert "orbit-core" in html
    assert "orbit-task-card" in html
    assert "orbit-connector" in html
    assert "orbit-card-bloom" in html
    assert "liquid-glass" in html
    assert "backdrop-filter" in html
    assert "categoryColors" in html
    assert "urgencyRadius" in html
    assert "connectorEndpoint" in html
    assert "orbit-map-stage" in html
    assert "orbit-connector-path" in html
    assert "orbit-connector-particles" in html
    assert "wavyConnectorPath" in html
    assert "stroke-dasharray:1 13" in html
    assert ".orbit-connector-layer { position:absolute; left:50%; top:50%; width:1px; height:1px; z-index:4" in html
    assert ".orbit-core { position:absolute; left:50%; top:50%; z-index:6" in html
    assert "background:transparent;" in html
    assert "生活" in html and "工作" in html and "自媒体" in html
    assert "class=\"donut\"" not in html
    assert "overview-metrics" not in html
    assert "overview-orbit-map::before" not in html
    assert "overview-orbit-map::after" not in html
    assert "overview-kicker" not in html
    assert "orbit-caption" not in html
    assert "Material Orbit" not in html
    assert "离中心越近，越需要优先处理" not in html
    assert "orbit-core-dot" not in html
    assert "orbit-label" not in html
    assert "待推进原点" not in html
    assert "<div class=\"orbit-core-inner\"><div class=\"orbit-count\">" in html
    assert "overview-dashboard" in html
    assert "overview-left-stage" in html
    assert "overview-greeting" in html
    assert "令辉" in html
    assert "overview-side-panel" in html
    assert "glass-card calendar-card" in html
    assert "renderOverviewSidePanel" in html
    assert "renderOverviewCalendar" in html
    assert "renderYesterdayLineChart" in html
    assert "renderYesterdayCategoryDonut" in html
    assert "renderYesterdayCarryover" in html
    assert "昨日电脑使用" in html
    assert "昨日时间比例" in html
    assert "昨日遗留任务" in html
    assert "translateX(-" in html
    assert "--bg: #000000" in html
    assert "body { margin: 0; color: var(--text); background:#000000" in html
    assert "grid-template-columns:minmax(0,1fr) minmax(340px,.38fr)" in html
    assert "grid-template-rows:290px 210px auto" in html
    assert "time-line-card, .category-donut-card { min-height:0; height:210px" in html
    assert "calendar-month-title" in html
    assert "calendar-month-grid" in html
    assert "calendar-weekdays" in html
    assert "calendar-day-cell active" in html
    assert "一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日" in html
    assert "global-glass-surface" in html


def test_visual_regressions_keep_glass_subtle_priority_colored_orbit_spacious_and_weekly_unwarped():
    module = load_module()
    html = module.render_html(sample_history())

    assert "--glass-bg: rgba(255,255,255,.072)" in html
    assert "--glass-bg-strong: rgba(255,255,255,.095)" in html
    assert "rgba(255,255,255,.16)" not in html
    assert "rgba(255,255,255,.22)" not in html
    assert ".task-card.priority-card-P0" in html
    assert ".task-card.priority-card-P1" in html
    assert ".task-card.priority-card-P2" in html
    assert ".task-card.priority-card-P3" in html
    assert "border-color:color-mix(in srgb, var(--priority-color), rgba(255,255,255,.12) 58%)" in html
    assert "layoutOrbitOverview" in html
    assert "data-orbit-angle" in html
    assert "data-orbit-radius" in html
    assert "--orbit-spread-x" in html
    assert "--orbit-spread-y" in html
    assert "window.addEventListener('resize', layoutOrbitOverview)" in html
    assert ".span-7 { grid-column: span 7; }" in html
    assert ".span-5 { grid-column: span 5; }" in html
    assert ".span-7, .span-5" in html


def test_top_nav_has_overview_task_panel_and_time_energy_management():
    module = load_module()
    html = module.render_html(sample_history())

    assert "top-nav" in html
    assert "top-tabs" in html
    assert "history-rail" in html
    assert "main-layout" in html
    assert "document.body.dataset.mode = mode" in html
    assert "<aside class=\"sidebar\">" not in html
    assert "首屏只回答一个问题" not in html
    assert "下面再继续处理任务、复盘和时间数据" not in html
    assert "<h1>令辉的工作台</h1>" in html
    assert "data-tab=\"overview\">总览</button>" in html
    assert "data-tab=\"workbench\">任务面板</button>" in html
    assert "data-tab=\"days\">时间/精力管理</button>" in html
    assert "data-tab=\"workbench\">工作台</button>" not in html
    assert "data-tab=\"days\">时间与精力</button>" not in html
    assert "data-tab=\"weeks\">每周</button>" not in html
    assert "body[data-mode=\"overview\"] .history-rail { display:none; }" in html
    assert "body[data-mode=\"workbench\"] .history-rail { display:none; }" in html


def test_time_weekly_is_nested_under_time_energy_management_left_nav():
    module = load_module()
    html = module.render_html(sample_history())

    assert "timeModeSwitch" in html
    assert "data-kind=\"time-section\" data-key=\"days\"" in html
    assert "data-kind=\"time-section\" data-key=\"weeks\"" in html
    assert ">每天</button>" in html
    assert ">每周</button>" in html
    assert "tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.tab === (mode === 'overview' ? 'overview' : (mode === 'workbench' ? 'workbench' : 'days'))))" in html
    assert "if (node.dataset.kind === 'time-section')" in html
    assert "mode = node.dataset.key === 'weeks' ? 'weeks' : 'days';" in html
    assert "switchMode('days')" in html


def test_overview_top_nav_page_is_strategy_goal_dashboard_with_gantt_progress():
    module = load_module()
    workbench = {
        "tasks": [
            {"id": "t1", "title": "补作品集", "priority": "P1", "category": "工作", "progress": 50, "goalId": "work", "directionId": "work-ability", "due": "2026-07-20"},
            {"id": "t2", "title": "口播练习", "priority": "P2", "category": "自媒体", "progress": 100, "goalId": "media", "directionId": "media-expression", "due": "2026-07-22"},
        ],
        "goals": [
            {"id": "life", "domain": "生活", "statement": "把生活节奏稳定下来", "directions": [{"id": "life-rhythm", "title": "生活节奏"}]},
            {"id": "work", "domain": "工作", "statement": "进大厂实习", "directions": [{"id": "work-ability", "title": "专业能力"}]},
            {"id": "media", "domain": "自媒体", "statement": "形成稳定内容输出", "directions": [{"id": "media-expression", "title": "语言表达"}]},
        ],
    }
    html = module.render_html(sample_history(), workbench)

    assert "renderOverview" in html
    assert "strategy-goal-first-fold" in html
    assert "strategy-hero-copy" in html
    assert "抬头看山，低头赶路" in html
    assert "目标不必天天仰望，路要日日去走" in html
    assert "strategy-domain-card" in html
    assert "生活" in html and "工作" in html and "自媒体" in html
    assert "进大厂实习" in html
    assert "goal-progress-ring" in html
    assert "renderGoalTree" in html
    assert "战略目标 → 二级方向 → 具体子任务" in html
    assert "strategy-tree-bottom" in html
    assert "goalId" in html and "directionId" in html
    assert "computeGoalProgress" in html
    assert "renderTaskGantt" in html
    assert "progress-gantt-card" in html
    assert "任务进度甘特图" in html
    assert "gantt-task-list" in html and "gantt-timeline-grid" in html and "gantt-bar" in html
    assert "openStrategyEditor" in html
    assert "编辑战略目标" in html
    assert "strategyEditorModal" in html
    assert "saveStrategyGoals" in html
    assert "strategy-editor-grid" in html
    assert "新增子任务" in html
    assert "openTaskModal(null, '${goal.id}', '${direction.id}')" in html
    assert "overview-deadline-card" not in html
    render_overview_block = html[html.index("function renderOverview()"):html.index("function renderWorkbench()")]
    assert "renderStrategyOverview(tasks)" in render_overview_block
    assert "renderTaskGantt(tasks)" in render_overview_block
    assert "workbenchList" not in html
    assert "workbenchSection" not in html
    assert "data-kind=\"workbench-section\"" not in html


def test_workbench_json_preserves_strategy_goals_for_task_attachment():
    module = load_module()
    normalized = module.normalize_workbench_data({
        "tasks": [{"title": "A", "goalId": "work", "directionId": "work-ability"}],
        "goals": [{"id": "work", "domain": "工作", "statement": "进大厂实习", "directions": [{"id": "work-ability", "title": "专业能力"}]}],
    })

    assert normalized["goals"][0]["id"] == "work"
    assert normalized["goals"][0]["directions"][0]["id"] == "work-ability"
    assert normalized["tasks"][0]["goalId"] == "work"
    assert normalized["tasks"][0]["directionId"] == "work-ability"


def test_workbench_keeps_large_overview_card_in_original_first_position():
    module = load_module()
    html = module.render_html(sample_history())

    render_workbench_block = html[html.index("function renderWorkbench()"):html.index("function renderDay(day)")]
    assert "overview-first-fold" in render_workbench_block
    assert "${renderOrbitOverview(tasks, stats)}" in render_workbench_block
    assert render_workbench_block.index("overview-first-fold") < render_workbench_block.index("todo-center-card")
    assert "overview-deadline-card" not in render_workbench_block


def test_workbench_second_fold_pairs_reminders_and_time_preview_on_the_right(): 
    module = load_module()
    html = module.render_html(sample_history())

    todo_article = "<article class=\"card span-8 next-fold todo-center-card\"><h3 class=\"section-title\">待办中心</h3>"
    reminder_article = "<article class=\"card span-4 next-fold task-reminder-card\"><h3 class=\"section-title\">任务提醒</h3>"
    review_article = "<article class=\"card span-6 review-card\"><h3 class=\"section-title\">今日复盘</h3>"
    preview_article = "<article class=\"card span-6 time-preview-card\"><h3 class=\"section-title\">时间与精力板块预览</h3>"

    assert todo_article in html
    assert reminder_article in html
    assert review_article in html
    assert preview_article in html
    assert html.index(todo_article) < html.index(reminder_article)
    assert html.index(review_article) < html.index(preview_article)
    assert ".todo-center-card .task-card { grid-template-columns:4px 46px minmax(180px,1fr) 74px minmax(210px,280px);" in html
    assert ".task-reminder-card { align-self:start; }" in html
    assert ".review-card, .time-preview-card { align-self:stretch; }" in html
    assert ".time-preview-card { display:flex; flex-direction:column; }" in html
    assert ".time-preview-card .kpis { grid-template-columns:repeat(2,minmax(0,1fr)); }" in html


def test_dropdown_menus_keep_options_readable_on_native_light_popups():
    module = load_module()
    html = module.render_html(sample_history())

    assert "select option { color:#111827; background:#ffffff; }" in html
    assert "select option:checked { color:#ffffff; background:#2563eb; }" in html
    assert "select option:hover { color:#111827; background:#dbeafe; }" in html


def test_add_task_form_is_modal_and_deadline_timeline_replaces_day_agenda():
    module = load_module()
    html = module.render_html(sample_history())

    assert "openTaskModal" in html
    assert "closeTaskModal" in html
    assert "task-modal-backdrop" in html
    assert "taskModal" in html
    assert "add-task-trigger" in html
    assert "任务进度甘特图" in html
    assert "renderTaskGantt" in html
    assert "任务提醒" in html
    assert "工作台洞察" not in html
    assert "今日时间轴" not in html
    assert "add-task-panel" not in html


def test_deadline_view_can_toggle_between_list_and_calendar():
    module = load_module()
    html = module.render_html(sample_history())

    assert "deadlineViewMode" in html
    assert "setDeadlineView" in html
    assert "renderDeadlineCalendar" in html
    assert "deadline-calendar" in html
    assert "日历视图" in html
    assert "列表视图" in html
    assert "距截止" in html


def test_tasks_are_color_ranked_draggable_cards_with_progress_slider():
    module = load_module()
    html = module.render_html(sample_history())

    assert "priority-card-P0" in html
    assert "priority-card-P1" in html
    assert "priority-card-P2" in html
    assert "task-drag-zone" in html
    assert "grip-lines" in html
    assert html.count("grip-line") >= 3
    assert "task-drag-zone:hover" not in html
    assert ".task-drag-zone { min-height:100%; width:100%; justify-self:stretch; display:grid; place-content:center;" in html
    assert "draggable=\"true\"" in html
    assert "dragTaskId" in html
    assert "reorderTasks" in html
    assert "previewTaskProgress" in html
    assert "commitTaskProgress" in html
    assert "progress-slider" in html
    assert "type=\"range\"" in html
    assert "step=\"1\"" in html
    assert "oninput=\"previewTaskProgress" in html
    assert "onchange=\"commitTaskProgress" in html
    assert "0%" in html
    assert "100%" in html
    assert "updateTaskStatus('${task.id}', this.checked" not in html
    assert "task-card priority-card-${task.priority} ${task.status === 'done' ? 'done' : ''}\" draggable" not in html
    assert "<span class=\"badge p-${task.priority}\">${task.priority}</span>" not in html
    assert "<span class=\"badge\">${statusLabels[task.status] || task.status}</span>" not in html
    assert "taskFocus" not in html
    assert "toggleTaskFocus" not in html
    assert "edit-task-btn" in html
    assert "openTaskModal('${task.id}')" in html
    assert "task-edit-cell" in html
    assert "task-main h4" in html
    assert "task-category" in html
    assert "生活" in html
    assert "工作" in html
    assert "自媒体" in html
    assert "task.project || '未归属'" not in html
    assert "taskProject" not in html
    assert "同一优先级可包含多个任务" not in html
    assert "drop-hint" not in html
    assert ".progress-slider { width:100%; height:34px;" in html
    assert ".progress-slider::-webkit-slider-thumb { width:30px; height:30px;" in html
    assert ".task-modal { width:min(980px, 100%);" in html
    assert "plane-view-toolbar" in html
    assert "工作项视图" in html
    assert "aria-pressed" in html
    assert "PWB-${String(index + 1).padStart(3, '0')}" in html
    assert "work-item-key" in html
    assert "property-pill" in html
    assert "progress-track" in html
    assert "--progress:${task.progress || 0}%" in html
    assert "input.style.setProperty('--progress'" in html
    assert "priority-accent" in html


def test_task_category_chips_are_clickable_filters_that_preserve_priority_lanes():
    module = load_module()
    html = module.render_html(sample_history())

    assert "let taskCategoryFilter = workbenchDraft.ui?.taskCategoryFilter || 'all'" in html
    assert "setTaskCategoryFilter" in html
    assert "taskCategories.map(category" in html
    assert "setTaskCategoryFilter('${category}')" in html
    assert "onclick=\"setTaskCategoryFilter('all')\"" in html
    assert "task-category-filter" in html
    assert "aria-pressed=\"${taskCategoryFilter === category}\"" in html
    assert "const visibleTasks = taskCategoryFilter === 'all' ? sorted : sorted.filter(task => (task.category || '工作') === taskCategoryFilter);" in html
    assert "visibleTasks.length" in html
    assert "const group = visibleTasks.filter(task => task.priority === priority);" in html
    assert "当前分类暂无待办" in html


def test_workbench_overview_is_priority_based_not_status_or_focus_based():
    module = load_module()
    html = module.render_html(sample_history())

    assert "priorityDonutStyle" in html
    assert "renderPriorityLegend" in html
    assert "P0 火烧屁股" in html
    assert "P1 今日必完成" in html
    assert "P2 常规推进" in html
    assert "P3 可延后" in html
    assert "待办 0" not in html
    assert "进行中 1" not in html
    assert "今日重点" not in html


def test_dragging_uses_priority_lanes_with_same_priority_and_cross_priority_feedback():
    module = load_module()
    html = module.render_html(sample_history())

    assert "priority-lane" in html
    assert "dropPriorityLane" in html
    assert "dragSourcePriority" in html
    assert "同优先级内排序" in html
    assert "松手切换" in html
    assert "同一优先级可包含多个任务" not in html
    assert "P0 火烧屁股" in html
    assert "P1 今日必完成" in html
    assert "P0 最高优先级" not in html
    assert "P1 高优先级" not in html
    assert "priorityForIndex" not in html


if __name__ == "__main__":
    test_render_html_upgrades_time_dashboard_into_personal_workbench()
    test_render_html_contains_editable_task_and_review_controls()
    test_workbench_data_uses_project_json_with_localstorage_migration_safety()
    test_workbench_json_is_staged_and_local_save_service_is_available()
    test_activitywatch_afk_away_time_is_excluded_before_daily_totals_and_categories()
    test_low_usage_days_are_saved_as_low_confidence_but_not_eligible_for_automation_push()
    test_automation_message_mentions_afk_removed_for_days_that_are_pushed()
    test_first_screen_is_a_radial_task_orbit_overview_not_a_donut()
    test_visual_regressions_keep_glass_subtle_priority_colored_orbit_spacious_and_weekly_unwarped()
    test_top_nav_has_overview_task_panel_and_time_energy_management()
    test_time_weekly_is_nested_under_time_energy_management_left_nav()
    test_overview_top_nav_page_is_strategy_goal_dashboard_with_gantt_progress()
    test_workbench_json_preserves_strategy_goals_for_task_attachment()
    test_workbench_keeps_large_overview_card_in_original_first_position()
    test_workbench_second_fold_pairs_reminders_and_time_preview_on_the_right()
    test_dropdown_menus_keep_options_readable_on_native_light_popups()
    test_add_task_form_is_modal_and_deadline_timeline_replaces_day_agenda()
    test_deadline_view_can_toggle_between_list_and_calendar()
    test_tasks_are_color_ranked_draggable_cards_with_progress_slider()
    test_task_category_chips_are_clickable_filters_that_preserve_priority_lanes()
    test_workbench_overview_is_priority_based_not_status_or_focus_based()
    test_dragging_uses_priority_lanes_with_same_priority_and_cross_priority_feedback()
    print("workbench render tests passed")
