#!/usr/bin/env python3
"""
编译前补丁脚本：修改 Git Commit 详情页的默认展开状态（Zed 上游无对应设置项）。

补丁点 1: commit_view.rs 多缓冲初始化改为“全部折叠”。
补丁点 2: commit_view.rs 提交描述 message_expanded 初始值改为 true。
补丁点 3: split.rs 删除 SplittableEditor::new 中无条件的 set_expand_all_diff_hunks
          调用。该调用会把补丁点 1 设置的折叠标志重置回“全部展开”
          （内部执行 multibuffer.set_all_diff_hunks_expanded），导致补丁点 1
          失效——异步 diff 加载走 DiffUpdated{base_changed:true} 路径，
          should_expand_hunk = was_previously_expanded || all_diff_hunks_expanded
          会把全部 hunk 重新展开。删除后对上游其他调用方无影响：
          - DiffMultibuffer（project_diff/staged_diff/branch_diff）自己在
            diff_multibuffer.rs 初始化时已调用 set_all_diff_hunks_expanded；
          - 上游 CommitView 也已在 commit_view.rs 初始化时显式设置（展开）；
          - split.rs 测试同样显式设置后再构造 SplittableEditor。
          即该行对所有既有调用方都是冗余的。

用法: python3 patch_commit_view_defaults.py [--source-root zed] [--dry-run]

设计约束：
- 幂等：重复执行不会叠加补丁（以补丁标记判断）。
- 显式失败：上游重构导致锚点消失时返回非 0，让 CI 暴露问题，
  而不是静默产出一个行为未改变的构建。
- 不改变任何交互能力：展开/折叠仍然可以通过内置动作
  editor::ExpandAllDiffHunks / CollapseAllDiffHunks / ToggleAllDiffHunks
  （命令面板与快捷键）以及提交描述的 Disclosure 按钮完成。
"""

import argparse
import io
import sys
from pathlib import Path

# Windows CI 默认 cp1252 编码，无法输出中文
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PATCH_MARKER = "[ZED_GLOBALIZATION_PATCH]"

# 补丁点 1 的主锚点：v1.21.0 中 CommitView::new 创建多缓冲时的展开调用。
# 同一文件内该调用仅出现一次。
EXPANDED_ANCHOR = "multibuffer.set_all_diff_hunks_expanded(cx);"
COLLAPSED_ANCHOR = "multibuffer.set_all_diff_hunks_collapsed(cx);"
# 兜底锚点：上游若改动初始化写法，退回到在 MultiBuffer::new 之后注入。
FALLBACK_ANCHOR = "MultiBuffer::new(Capability::ReadOnly);"

# 补丁点 2 的锚点：CommitView 结构体中提交描述的展开状态初始值。
MESSAGE_EXPANDED_ANCHOR = "message_expanded: false,"

# 补丁点 3 的锚点：SplittableEditor::new 中把多缓冲强制置回“全部展开”的调用。
# 该调用发生在 CommitView::new（含补丁点 1）之后，会覆盖折叠标志，必须删除。
# 整个仓库中该调用仅此一处（split.rs）。
EXPAND_ALL_CALL_ANCHOR = "editor.set_expand_all_diff_hunks(cx);"

COMMIT_VIEW_PATH = "crates/git_ui/src/commit_view.rs"
MULTI_BUFFER_PATH = "crates/multi_buffer/src/multi_buffer.rs"
MULTI_BUFFER_API = "pub fn set_all_diff_hunks_collapsed(&mut self, cx: &mut Context<Self>)"
SPLIT_PATH = "crates/editor/src/split.rs"


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str, dry_run: bool, name: str) -> None:
    if dry_run:
        print(f"  DRY-RUN: {name} 将被修改")
    else:
        # 写字节而非文本，避免在 Windows 上触发 \n -> \r\n 转换
        path.write_bytes(content.encode("utf-8"))
        print(f"  OK: {name} 补丁成功")


def _check_collapse_api(source_root: Path) -> bool:
    """确认上游存在 set_all_diff_hunks_collapsed，避免替换成不存在的 API。"""
    content = _read(source_root / MULTI_BUFFER_PATH)
    if content is None:
        print(f"  WARN: 未找到 {MULTI_BUFFER_PATH}，无法确认折叠 API 是否存在")
        return False
    if MULTI_BUFFER_API not in content:
        print(f"  WARN: {MULTI_BUFFER_PATH} 中未找到 {MULTI_BUFFER_API.strip()}，上游可能已重构")
        return False
    return True


def patch_collapse_files_by_default(source_root: Path, dry_run: bool) -> bool:
    """补丁点 1: 文件 diff 默认折叠。

    将 CommitView::new 中多缓冲的“全部展开”改为“全部折叠”。
    """
    target = source_root / COMMIT_VIEW_PATH
    content = _read(target)
    if content is None:
        print(f"  WARN: 未找到 {COMMIT_VIEW_PATH}（上游可能移动了文件）")
        return False

    if "commit 详情页默认折叠" in content:
        print(f"  SKIP: {target.name} 已包含补丁点 1 标记，跳过")
        return True

    if EXPANDED_ANCHOR in content and content.count(EXPANDED_ANCHOR) == 1:
        replacement = (
            f"// {PATCH_MARKER} commit 详情页默认折叠所有文件的 diff 变更块\n"
            f"            {COLLAPSED_ANCHOR}"
        )
        patched = content.replace(EXPANDED_ANCHOR, replacement, 1)
        _write(target, patched, dry_run, target.name)
        return True

    if FALLBACK_ANCHOR in content and content.count(FALLBACK_ANCHOR) == 1:
        # 上游改写了初始化：仍在唯一创建点之后注入折叠调用。
        replacement = (
            f"let mut multibuffer = {FALLBACK_ANCHOR}\n"
            f"            // {PATCH_MARKER} commit 详情页默认折叠所有文件的 diff 变更块\n"
            f"            {COLLAPSED_ANCHOR}"
        )
        patched = content.replace(
            f"let mut multibuffer = {FALLBACK_ANCHOR}", replacement, 1
        )
        _write(target, patched, dry_run, target.name)
        return True

    print("  WARN: 未找到多缓冲初始化锚点，上游可能已重构，跳过")
    return False


def patch_expand_commit_message_by_default(source_root: Path, dry_run: bool) -> bool:
    """补丁点 2: 提交描述默认展开。

    将 CommitView 结构体中 message_expanded 的初始值改为 true。
    """
    target = source_root / COMMIT_VIEW_PATH
    content = _read(target)
    if content is None:
        print(f"  WARN: 未找到 {COMMIT_VIEW_PATH}")
        return False

    if "commit 详情页默认展开提交描述" in content:
        print(f"  SKIP: {target.name} 已包含补丁点 2 标记，跳过")
        return True

    if content.count(MESSAGE_EXPANDED_ANCHOR) != 1:
        print(
            f"  WARN: 锚点 {MESSAGE_EXPANDED_ANCHOR!r} 出现 {content.count(MESSAGE_EXPANDED_ANCHOR)} 次，"
            "预期恰好 1 次，跳过以免误改"
        )
        return False

    replacement = (
        f"// {PATCH_MARKER} commit 详情页默认展开提交描述\n"
        f"            message_expanded: true,"
    )
    patched = content.replace(MESSAGE_EXPANDED_ANCHOR, replacement, 1)
    _write(target, patched, dry_run, target.name)
    return True


def patch_remove_forced_expand(source_root: Path, dry_run: bool) -> bool:
    """补丁点 3: 删除 SplittableEditor::new 中无条件的 set_expand_all_diff_hunks。

    不删除会导致补丁点 1 被覆盖（标志重置回 true，diff 加载后全部展开）。
    """
    target = source_root / SPLIT_PATH
    content = _read(target)
    if content is None:
        print(f"  WARN: 未找到 {SPLIT_PATH}（上游可能移动了文件）")
        return False

    idempotency_marker = f"{PATCH_MARKER} 移除 SplittableEditor 强制展开"
    if idempotency_marker in content:
        print(f"  SKIP: {target.name} 已包含补丁点 3 标记，跳过")
        return True

    if content.count(EXPAND_ALL_CALL_ANCHOR) != 1:
        print(
            f"  WARN: 锚点 {EXPAND_ALL_CALL_ANCHOR!r} 出现 {content.count(EXPAND_ALL_CALL_ANCHOR)} 次，"
            "预期恰好 1 次，跳过以免误删"
        )
        return False

    # 注意：注释中不能保留锚点子串，否则二次执行会重复替换（幂等破坏）。
    replacement = (
        f"// {PATCH_MARKER} 移除 SplittableEditor 强制展开：该调用会把多缓冲标志重置回全部展开，\n"
        f"            // 覆盖 commit 详情页默认折叠补丁；上游所有 SplittableEditor::new 调用方\n"
        f"            // 均已在构造前显式设置展开状态，此行冗余。\n"
    )
    patched = content.replace(EXPAND_ALL_CALL_ANCHOR, replacement, 1)
    _write(target, patched, dry_run, target.name)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="编译前补丁：commit 详情页默认折叠文件 diff 并展开提交描述"
    )
    parser.add_argument(
        "--source-root",
        default="zed",
        help="Zed 源码根目录（默认: zed）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅检查，不实际修改文件",
    )
    args = parser.parse_args()

    source_root = Path(args.source_root)
    if not source_root.is_dir():
        print(f"ERROR: 源码目录 {source_root} 不存在")
        return 1

    print(f"源码目录: {source_root.resolve()}")
    if args.dry_run:
        print("模式: dry-run（不修改文件）\n")
    else:
        print("模式: 正式补丁\n")

    print("[补丁 0] 校验上游折叠 API 存在")
    api_ok = _check_collapse_api(source_root)

    print("[补丁 1] 文件 diff 默认折叠")
    r1 = patch_collapse_files_by_default(source_root, args.dry_run) if api_ok else False

    print("[补丁 2] 提交描述默认展开")
    r2 = patch_expand_commit_message_by_default(source_root, args.dry_run)

    print("[补丁 3] 删除 SplittableEditor 强制展开（防止补丁 1 被覆盖）")
    r3 = patch_remove_forced_expand(source_root, args.dry_run)

    print()
    if r1 and r2 and r3:
        print("全部补丁已就绪。")
        return 0
    else:
        print("部分补丁未能应用，请检查上方 WARN 信息。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
