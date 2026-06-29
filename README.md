# computer-time-dashboard

本项目用于生成“电脑时间与精力分布”可视化看板。

## 当前能力
- 每日 19:30 输出当天复盘
- 若 19:30 电脑未开机，Hermes 下次运行后自动补发上一日
- 每周日自动附带周总结
- 使用 ActivityWatch 本地数据库作为数据源
- 生成可直接部署到 GitHub Pages 的静态页面：`site/index.html`

## 目录
- `scripts/sync_dashboard.py`：主脚本，读取数据、更新历史、生成 HTML、可选 Git 推送
- `data/history.json`：累计的日/周历史数据
- `data/state.json`：已发送日期状态，避免重复推送
- `site/index.html`：最终看板页面

## GitHub Pages 建议
将仓库推送到 GitHub 后，把 Pages 源设置为：
- Branch: `main`
- Folder: `/site`

## 本地手动运行
```bash
python3 scripts/sync_dashboard.py --print-dry-run --skip-push
```

## 强制重建某一天
```bash
python3 scripts/sync_dashboard.py --force-date 2026-06-29 --skip-push
```
