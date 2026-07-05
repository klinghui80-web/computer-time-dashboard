from __future__ import annotations

import importlib.util
import sys
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


def test_add_task_form_is_modal_and_deadline_timeline_replaces_day_agenda():
    module = load_module()
    html = module.render_html(sample_history())

    assert "openTaskModal" in html
    assert "closeTaskModal" in html
    assert "task-modal-backdrop" in html
    assert "taskModal" in html
    assert "add-task-trigger" in html
    assert "任务截止时间轴" in html
    assert "renderDeadlineTimeline" in html
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

    assert "let taskCategoryFilter = localStorage.getItem('taskCategoryFilter') || 'all'" in html
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
    test_first_screen_is_a_radial_task_orbit_overview_not_a_donut()
    test_add_task_form_is_modal_and_deadline_timeline_replaces_day_agenda()
    test_deadline_view_can_toggle_between_list_and_calendar()
    test_tasks_are_color_ranked_draggable_cards_with_progress_slider()
    test_task_category_chips_are_clickable_filters_that_preserve_priority_lanes()
    test_workbench_overview_is_priority_based_not_status_or_focus_based()
    test_dragging_uses_priority_lanes_with_same_priority_and_cross_priority_feedback()
    print("workbench render tests passed")
