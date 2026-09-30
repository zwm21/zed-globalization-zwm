# 方案：Git 面板点击 commit 弹出的新标签页，默认折叠所有文件 diff、默认展开提交描述

> 研究对象：Zed 上游源码，标签 `v1.21.0`（2026-09-30 查询 releases API 得到的最新稳定版）。
> 本仓库是 l10n 流水线仓库，不含 Zed 源码；源码在构建时由 CI 克隆上游后打补丁。因此改动必须
> 以"补丁脚本 + CI 接入"的形式交付，不能直接改 Zed 源码。

## 结论摘要

1. Zed 上游**没有**任何现成设置项可以改变这两个默认状态（详见下节证据）。设置里唯一相近的
   是 `git_panel.collapse_untracked_diff`，它只影响 git 面板里"未跟踪文件"的 diff，与
   commit 详情页无关。
2. 源码中两处初始值即可达成目标，改动共 2 行语义变更，且**不删减任何交互能力**：
   - 文件 diff 默认折叠：`crates/git_ui/src/commit_view.rs:307` 的
     `multibuffer.set_all_diff_hunks_expanded(cx);` 改为 `set_all_diff_hunks_collapsed(cx);`
   - 提交描述默认展开：`crates/git_ui/src/commit_view.rs:520` 的
     `message_expanded: false,` 改为 `message_expanded: true,`
3. 已在工作区实现可复用补丁脚本 `patch_commit_view_defaults.py`（仿 `patch_agent_env.py`
   的约定：`--source-root` / `--dry-run`、补丁标记、幂等、上游锚点失效时退出码 1），并用
   v1.21.0 真实源码夹具验证：补丁可干净应用、重复执行幂等、上游改名时显式失败、
   `rustfmt --check` 通过（语法有效且格式与上游一致）。
4. **影响面比需求稍大的一点已确认并接受**：`CommitView::new` 同时服务 commit 详情页与
   stash 详情页（`commit_view.rs:1388` 的 `key_context` 在两者间切换），因此 stash 标签页
   也会同样变为默认折叠+展开描述。二者是同一套 UI，行为一致更自然。
5. 上游不存在现成开关，故以"补丁脚本 + CI 接入"交付；CI 接入片段见第五节。

## 一、"没有现成设置"的证据

- `crates/git_ui/src/git_panel_settings.rs`（v1.21.0）中 `GitPanelSettings` 的字段全集为：
  button / dock / default_width / status_style / file_icons / folder_indicator / scrollbar /
  fallback_branch_name / sort_by / group_by / collapse_untracked_diff / tree_view / diff_stats /
  show_count_badge / starts_open / commit_title_max_length / entry_primary_click_action。
  没有任何一项控制 commit 详情页的展开状态。
- `assets/settings/default.json` 的 `git_panel` 块与上述字段一一对应，无隐藏项；
  `collapse_untracked_diff` 默认 `false`，其使用点在 `crates/git_ui/src/diff_multibuffer.rs`
  （第 127-150、562-568 行）：仅对"未跟踪文件 + 已删除文件"在插入 excerpt 后调用
  `editor.fold_buffers(...)`。commit 详情页（`commit_view.rs`）不读取该设置。
- zed.dev 的 all-settings 文档中 git 相关设置（git_gutter、inline_blame、branch_picker、
  hunk_style、diff_base 等）同样不涉及 commit 详情页默认展开状态。

结论：**只能改源码默认值**。

## 二、源码研究结果（v1.21.0）

### 目标界面就是 `CommitView`

git 面板中点击某条 commit 会调用 `CommitView::open(...)`（`crates/git_ui/src/git_panel.rs`
第 6817、7038、7527 行），在新标签页中渲染 `CommitView`（`crates/git_ui/src/commit_view.rs`）。
`CommitView::render`（第 1383-1400 行）= 头部（作者/日期/SHA/提交描述 Disclosure）+ 多缓冲
diff 编辑器。

**同一 `CommitView::new` 还被 stash 详情页复用**：`commit_view.rs:1388` 处
`key_context(if is_stash { "StashDiff" } else { "CommitDiff" })` 表明同一结构体按
`stash: Option<usize>` 切换两种页签语义。因此本次两处初始值改动会同时作用于：

- commit 详情页（需求目标）；
- stash 详情页（连带影响，已与需求方确认为可接受——同一套 UI 行为一致更自然）。

若将来只想改 commit、不动 stash，则需把折叠调用下移到 `if self.stash.is_none()` 分支或
渲染层条件判断，实现更绕；本次不做。

### 文件 diff 的默认展开状态

`CommitView::new` 创建只读 `MultiBuffer` 后立即调用：

```rust
let multibuffer = cx.new(|cx| {
    let mut multibuffer = MultiBuffer::new(Capability::ReadOnly);
    multibuffer.set_all_diff_hunks_expanded(cx);   // 第 307 行：默认全部展开
    multibuffer
});
```

`MultiBuffer::set_all_diff_hunks_collapsed(&mut self, cx)` 是该 API 的镜像
（`crates/multi_buffer/src/multi_buffer.rs:2343-2346`）：先把快照标志
`all_diff_hunks_expanded` 置为 `false`，再对全缓冲执行折叠。

把调用提前到"还没有任何 excerpt"时是有效的，机制在 `multi_buffer.rs`：

- `recompute_diff_transforms_for_edit`（第 2840 行起）对每个 hunk 计算
  `should_expand_hunk = was_previously_expanded || all_diff_hunks_expanded`
  （第 2972-2987 行，`DiffUpdated` 与 `BufferEdited` 两个分支都是这个结果）。
- 第 2877-2883 行：`BufferEdited` 且没有历史展开 hunk 且标志为 false 时直接跳过展开。
- `CommitView` 加载顺序是先 `update_excerpts_for_path` 插入各文件 excerpt，再 `add_diff`
  注册 diff（`commit_view.rs:458-471` 与 `crates/editor/src/split.rs:1212-1230`），
  随后 `add_diff` 触发 `DiffUpdated` 路径重算 transforms——此时标志为 false，hunk 保持折叠。
- 新建文件的 hunk 在标志为 false 时会被跳过（第 2939 行）与不列入 hunk 列表（第 3494 行），
  即表现为"只显示文件头一行，可点击展开"，正是目标状态。

`CommitView` 的异步加载任务（`commit_view.rs:344-499`）不会重新展开 hunk；其中唯一的折叠
调用 `editor.fold_buffers(binary_buffer_ids, cx)`（第 492 行）只针对二进制文件，与本次改动正交。

### 提交描述的默认展开状态

字段 `message_expanded: bool`（`commit_view.rs:80`），初始值 `false`（第 520 行）。
渲染侧（第 755-760 行 Disclosure 按钮、第 875-902 行消息区）：

- 仅当描述含换行（`has_more`）时才显示 Disclosure 三角按钮，点击切换 `message_expanded`。
- `collapsed = has_more && !is_expanded`：折叠时高度 1 行并裁切；展开时最高 12 行、可滚动。
- 分裂视图通过 `clone_on_split` 继承该字段（第 1366 行），逻辑一致。

因此把初始值改为 `true` 即"默认展开提交描述"； Disclosure 按钮、按钮文案
（Fold/Expand Commit Description）与分裂行为都不变。

## 三、最小改动内容（2 处、2 行语义变更）

```diff
--- a/crates/git_ui/src/commit_view.rs
+++ b/crates/git_ui/src/commit_view.rs
@@ -305,7 +305,8 @@ impl CommitView {
         let multibuffer = cx.new(|cx| {
             let mut multibuffer = MultiBuffer::new(Capability::ReadOnly);
-            multibuffer.set_all_diff_hunks_expanded(cx);
+            // [ZED_GLOBALIZATION_PATCH] commit 详情页默认折叠所有文件的 diff 变更块
+            multibuffer.set_all_diff_hunks_collapsed(cx);
             multibuffer
         });
@@ -518,7 +519,8 @@ impl CommitView {
             message,
-            message_expanded: false,
+            // [ZED_GLOBALIZATION_PATCH] commit 详情页默认展开提交描述
+            message_expanded: true,
             message_scroll_handle: ScrollHandle::new(),
```

补丁脚本 `patch_commit_view_defaults.py` 生成的结果与上面完全一致（已在 v1.21.0 夹具上 diff 验证）。

## 四、为什么不会影响软件功能

1. 折叠/展开是**纯展示初始状态**，用户随时可以改回，且入口本来就是 Zed 自带能力：
   - `editor::CollapseAllDiffHunks` / `editor::ExpandAllDiffHunks` /
     `editor::ToggleAllDiffHunks` 均已注册为动作（`crates/editor/src/actions.rs:526-533`），
     实现见 `crates/editor/src/git.rs:982-1004`：折叠就是对同一 multibuffer 执行
     `collapse_diff_hunks(vec![Anchor::Min..Anchor::Max], cx)`——与本次改动的初始状态是同一状态。
   - 这些动作在命令面板可用，且有默认快捷键：Linux/Windows `ctrl-"` 展开全部、`ctrl-'`
     切换选中 hunk；macOS `cmd-"` / `cmd-'`（`assets/keymaps/default-{linux,macos,windows}.json`）。
   - commit 详情页的 key context 为 `CommitDiff`（`commit_view.rs:1388`），内嵌编辑器获得焦点时
     这些 Editor 上下文动作依然作用于该视图（键位分发表按上下文路径匹配，不缺省失效）。
2. `message_expanded` 不影响任何行为逻辑，只决定初始渲染高度；Disclosure 切换、
   `clone_on_split` 继承均不受影响。
3. 改动不触碰 Git 数据加载、diff 计算、askpass、暂存/提交等任何功能路径；CommitView 的
   异步任务、二进制文件折叠逻辑不变。
4. 补丁脚本对上游变化是"显式失败"而非"静默跳过"：若锚点消失或 `set_all_diff_hunks_collapsed`
     被改名/移除，脚本退出码 1，CI 的 prepare 作业随之失败，不会产出"以为改了其实没改"的包。
     （这与仓库既有 `patch_agent_env.py` 的失败语义一致。）

## 五、在本仓库的接入方式

`prepare` 作业（`.github/workflows/02-build.yml` 第 176-297 行）在克隆 Zed 后依次执行
`scripts/rebrand.py` → `zedl10n replace` → `patch_agent_env.py` → `cargo check` →
`git add -A && git diff --cached --binary > ../l10n.patch`。补丁内容由"与上游的 git diff"生成，
因此只要在 `cargo check` 之前加一步，改动就会自动进入 `l10n.patch`，各平台构建作业的
`git apply l10n.patch` 无需任何改动。

已接入的步骤（放在"应用 Agent 环境变量补丁"之后、"编译检查"之前）：

```yaml
      - name: 应用 commit 详情页默认状态补丁
        run: python3 patch_commit_view_defaults.py --source-root zed
```

时序上该步骤必须满足三点，缺一不可：

1. 在 `git clone` Zed 之后（有源码可改）；
2. 在 `zedl10n replace` 之后（翻译替换只改字符串字面量，不会覆盖本补丁改的两个非字符串锚点，
   先后顺序其实无害，但保持"品牌→翻译→行为补丁"的固定次序便于排查）；
3. 在 `git add -A && git diff --cached --binary > ../l10n.patch` **之前**——否则改动不会进入
   `l10n.patch`，各平台构建作业克隆的是干净上游源码并 `git apply l10n.patch`，改不到它们。

### 上线前的远端验证顺序

`prepare` 作业自带 `cargo check`（`skip_check != true` 时执行），即补丁的编译级验证由它兜底，
不必在本地编译 Zed。推荐按此顺序降低风险：

1. **小范围验证**：Actions →「02 构建发行」→ Run workflow，参数
   `arch=windows`（或 `linux-x86_64`）、`publish=actions`、`skip_check=false`、`release=true`。
   `publish=actions` 使产物只进 Artifact 不发 Release；`skip_check=false` 保持编译检查开启。
2. 下载 Artifact 里的 zip，本地实机确认：git 面板点 commit → 新标签页文件默认折叠、
   提交描述默认展开；按 `ctrl-"`/`cmd-"` 能全部展开回来；stash 页签行为一致（已知连带影响）。
3. 确认无误后再按 `arch=all` + `publish=release` 正式发布。

注意 `concurrency.group: build` 且 `cancel-in-progress: false`，触发是排队而非取消，
不要重复点。

### 回滚

删掉该 yaml 步骤即可，下一次构建即恢复上游默认行为；已发布的带新行为的版本不受影响
（用户手里的包不会变回去），如需"回到旧行为"只能靠发一版没有该补丁的新构建。

## 六、备选方案（本次不采用，仅记录）

- **方案 B：新增 Zed 设置项**。在 `GitPanelSettings` 增加两个字段并在 `assets/settings/default.json`
  给默认值，再在 `commit_view.rs` 读取。工作量与跨版本维护成本明显更高（settings、JSON schema、
  默认值、读取点四处联动），且与"改默认值"的目标相比收益有限；若将来上游自己加了开关，
  应改为读取上游设置。
- **方案 C：等上游支持**。目前未发现上游有相关设置或明确 issue，不作为依据。

## 七、本地验证证据（本轮完成）

用 v1.21.0 真实源码（`crates/git_ui/src/commit_view.rs`、`crates/multi_buffer/src/multi_buffer.rs`）
搭建夹具执行 `patch_commit_view_defaults.py`：

| 场景 | 结果 |
| --- | --- |
| `--dry-run` | 两个补丁点均识别到锚点，不落盘 |
| 正式执行 | 两处补丁成功，退出码 0；与原文件 diff 仅 2 处语义变更、换行符保持 LF 不变 |
| 二次执行（幂等） | 两处均 SKIP，退出码 0 |
| 上游把 `set_all_diff_hunks_collapsed` 改名 | 补丁点 0 报 WARN，补丁点 1 不执行，退出码 1 |
| 上游改写多缓冲初始化（主锚点消失） | 走兜底锚点注入 `MultiBuffer::new(Capability::ReadOnly)` 之后，退出码 0 |
| `rustfmt --check`（edition 2024，补丁后文件） | 通过，语法有效且格式与上游一致（原文件同样通过，故检查有效） |
| 换到上游 `main` 分支重跑（2026-09-30） | 两个锚点行号与 v1.21.0 完全一致（307 / 520），补丁同样干净应用且 `rustfmt --check` 通过——短期内上游版本迭代不会让补丁失效 |

未做（环境限制，需 CI 兜底）：在本机编译 Zed；工作区不含 Zed 源码，克隆整仓进行研究超出
"只在工作区内活动"的约束。编译级验证依赖 `prepare` 作业现有的 `cargo check` 步骤。

## 八、复现研究的入口

- 上游源码（tag v1.21.0）：
  `https://raw.githubusercontent.com/zed-industries/zed/v1.21.0/crates/git_ui/src/commit_view.rs`
  以及 `crates/multi_buffer/src/multi_buffer.rs`、`crates/git_ui/src/git_panel_settings.rs`、
  `crates/editor/src/{actions,git,split}.rs`、`assets/settings/default.json`、
  `assets/keymaps/default-linux.json`。
