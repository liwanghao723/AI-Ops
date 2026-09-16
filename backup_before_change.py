#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
改前自动备份脚本 —— H3C 云桌面智能运维助手

在修改源码 / 重新打包之前运行本脚本，会把当前「可运行 + 可重建」的最小必要集合
打包成【单独一个 .zip 文件】，便于改坏后一键回退。

包含：
  - H3COpsAssistant.exe       可运行回退副本（来自 dist/）
  - build.spec                PyInstaller 构建配置
  - requirements.txt          Python 依赖清单
  - src/                      完整源码快照（自动剔除 __pycache__）
  - tests/                    完整测试套件快照（自动剔除 __pycache__/.pytest_cache）
  - BACKUP_INFO.txt           本备份的元信息与回退说明

用法：
  py -3.14 backup_before_change.py                 # 默认备份当前工程（脚本所在目录）
  py -3.14 backup_before_change.py --dry           # 只预览将备份什么，不写文件
  py -3.14 backup_before_change.py --out D:/bak    # 指定输出目录

输出文件名：H3COpsAssistant_v1.0.0_YYYY-MM-DD_HHMM.zip
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# ---- 可配置路径（默认指向 v1.0.0 工程）-----------------------------------
DEFAULT_PROJECT = r"C:\Users\liwanghao\Desktop\AI工具文件\v1.0.0"
DEFAULT_OUTDIR = r"C:\Users\liwanghao\Desktop\AI工具文件\H3COpsAssistant_backups"
VERSION = "v1.0.0"

# 这些目录不进备份（运行期生成 / 冗余）
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "build", "dist",
                "logs", "config", "knowledge", "updates"}


def resolve_project(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve().parent
    if (here / "build.spec").exists():
        return here
    return Path(DEFAULT_PROJECT)


def collect_entries(project: Path):
    """返回 [(磁盘绝对路径, zip内相对路径), ...]"""
    entries: list[tuple[Path, str]] = []
    exe = project / "dist" / "H3COpsAssistant.exe"
    if exe.exists():
        entries.append((exe, "H3COpsAssistant.exe"))
    for name in ("build.spec", "requirements.txt"):
        f = project / name
        if f.exists():
            entries.append((f, name))
    src = project / "src"
    if src.is_dir():
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if p.is_file():
                entries.append((p, str(Path("src") / rel)))
    tests = project / "tests"
    if tests.is_dir():
        for p in sorted(tests.rglob("*")):
            rel = p.relative_to(tests)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if p.is_file():
                entries.append((p, str(Path("tests") / rel)))
    return entries


def make_info(project: Path, entries) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "H3C 云桌面智能运维助手 — 改前自动备份",
        f"版本: {VERSION}",
        f"备份时间: {now}",
        f"工程路径: {project}",
        "",
        "包含文件:",
    ]
    for _, zp in entries:
        lines.append(f"  - {zp}")
    lines += [
        "",
        "如何回退:",
        "  方式A（直接运行）: 解压后双击 H3COpsAssistant.exe",
        "  方式B（还原工程）: 把 H3COpsAssistant.exe 复制回 <工程>/dist/H3COpsAssistant.exe 覆盖",
        "",
        "如何重建:",
        "  cd <解压目录>",
        "  pip install -r requirements.txt",
        "  pyinstaller build.spec",
        "",
        "注意: 不含 config/knowledge/updates/logs 等运行期生成目录，回退无需它们。",
        "tests/ 已随包提供，后续更新源码后可直接 pytest 回归（QT_QPA_PLATFORM=offscreen）。",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="改前自动备份（单 zip 文件）")
    ap.add_argument("--project", default=None, help="工程根目录（默认脚本所在目录）")
    ap.add_argument("--out", default=None, help="输出目录（默认内置备份目录）")
    ap.add_argument("--dry", action="store_true", help="只预览，不写文件")
    args = ap.parse_args()

    project = resolve_project(args.project)
    if not (project / "build.spec").exists():
        print(f"[错误] 未在 {project} 找到 build.spec，请检查 --project 路径", file=sys.stderr)
        sys.exit(1)

    outdir = Path(args.out) if args.out else Path(DEFAULT_OUTDIR)
    entries = collect_entries(project)
    if not entries:
        print("[错误] 没有可备份的内容（dist/H3COpsAssistant.exe 缺失？）", file=sys.stderr)
        sys.exit(2)

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    zip_path = outdir / f"H3COpsAssistant_{VERSION}_{stamp}.zip"
    info_text = make_info(project, entries)

    print(f"工程: {project}")
    print(f"输出: {zip_path}")
    print(f"将打包 {len(entries)} 项 + BACKUP_INFO.txt")
    for _, zp in entries:
        print(f"   + {zp}")

    if args.dry:
        print("\n[DRY] 未写入文件。")
        return

    outdir.mkdir(parents=True, exist_ok=True)
    # 同分钟重复运行则追加秒，避免覆盖
    if zip_path.exists():
        stamp2 = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        zip_path = outdir / f"H3COpsAssistant_{VERSION}_{stamp2}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for disk, arc in entries:
            zf.write(disk, arc)
        zf.writestr("BACKUP_INFO.txt", info_text)
    size = zip_path.stat().st_size
    print(f"\n[完成] 已生成单文件备份: {zip_path} ({size/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
