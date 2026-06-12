# Tab UI 设计

## 布局结构
```
┌─────────────────────────────────────────┐
│  [/] filter                             │  filter bar
├─────────────────────────────────────────┤
│ [skills] [agents] [commands] [rules]    │  tab labels
│                                         │  content area
│  当前 tab 的组件列表                     │  (scrollable)
│   [ ] item-1                            │
│  > [ ] item-2                           │  cursor
│   [v] item-3 (已选中)                   │
│  ...                                    │
├─────────────────────────────────────────┤
│ [space] toggle  [tab] switch  [a/n] all │  bottom menu
│ [arrows] move  [/] filter  [enter] done │
└─────────────────────────────────────────┘
```

## 按键映射
| 键 | 动作 |
|----|------|
| `←`/`→` 或 `Tab`/`Shift+Tab` | 切换 tab |
| `↑`/`↓` 或 `j`/`k` | 在当前 tab 内移动光标 |
| `Space` | 切换当前 tab 内光标项的选中状态 |
| `a` | 全选当前 tab |
| `n` | 取消选择当前 tab |
| `/` | 进入 filter 模式 |
| `Esc` | 退出 filter 并清空 |
| `Enter` | 确认并退出 |
| `Q`/Ctrl+C | 中止安装 |

## 实现要点
1. `interactive_select` 不再遍历调用 `_select_category`，改为一次性显示所有 tab
2. 每个组件类型独立维护：
   - `items`: 可用列表
   - `selected`: 已选中集合
   - `cursor`: 当前光标位置
3. `filter_kw` 对所有 tab 同时生效
4. `_render_frame` 渲染：
   - filter bar（第 1 行）
   - tab labels（第 2 行）
   - 当前 tab 的内容（可滚动）
   - 底部菜单（最后 1-2 行）
