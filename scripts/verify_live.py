"""真实飞书 API 联调自检脚本。

用途：改了数据层代码之后，用真实密钥跑一遍，确认「活动发现 → 成员查询 → 学时汇总」
整条链路还通。**不写任何测试断言**，只打印可人工核对的结论；真正的回归靠 pytest。

用法：
    python scripts/verify_live.py                # 全量自检
    python scripts/verify_live.py --skip-query   # 只建注册表，不查具体社员

隐私：脚本只读取查询所需的最小字段；打印姓名时做脱敏，不会把社员个人信息打进日志。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SENSITIVE_FIELDS, load_settings  # noqa: E402
from feishu import FeishuHttpClient, FeishuError  # noqa: E402
from feishu import schema as S  # noqa: E402
from feishu.member_query import query_member  # noqa: E402
from feishu.registry import build_registry  # noqa: E402
from services.footprint import group_breakdown, summarize  # noqa: E402

LINE = "─" * 64


def mask(name: str) -> str:
    if not name:
        return "(空)"
    return name[0] + "＊" * max(0, len(name) - 1)


def pick_sample(client, spec) -> tuple[str, str] | None:
    """从主表随机取一条记录，拿它的姓名 + 学号当查询样本（打印时脱敏）。"""
    try:
        rows = client.search_records(
            spec.base_token,
            spec.main_table_id,
            field_names=["姓名", "学号"],
            limit=50,
        )
    except FeishuError as exc:
        print(f"  取样本失败：{exc}")
        return None

    for row in rows:
        fields = row.get("fields") or {}
        name = S.cell_text(fields.get("姓名"))
        sid = S.cell_text(fields.get("学号"))
        if name and sid:
            return name, sid
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-query", action="store_true", help="只建注册表")
    args = parser.parse_args()

    settings = load_settings()
    client = FeishuHttpClient(
        settings.app_id, settings.app_secret, max_workers=settings.max_workers
    )

    print(LINE)
    print("爱心足迹 · 真实接口自检")
    print(LINE)

    print("\n[1/4] 构建活动注册表 …")
    started = time.time()
    registry = build_registry(client, settings.bases, max_workers=settings.max_workers)
    elapsed = time.time() - started

    print(f"  耗时 {elapsed:.1f}s")
    print(f"  接口版本：{'base/v3（新版）' if client._api_version == 3 else 'bitable/v1（旧版）'}")
    print(f"  文件夹结构：{'不可用（已降级）' if client.blocks_degraded else '正常'}")
    print(f"  活动表总数：{len(registry.tables)}")

    for spec in settings.bases:
        tables = registry.tables_of(spec.key)
        grouped: dict[str, int] = {}
        for table in tables:
            grouped[table.color_key] = grouped.get(table.color_key, 0) + 1
        print(f"\n  ── {spec.label}：{len(tables)} 张活动表")
        if grouped:
            for group, count in sorted(grouped.items(), key=lambda kv: -kv[1]):
                print(f"       {group:<12} {count:>4} 张")

        no_hours = [t for t in tables if not t.hours_fields]
        no_time = [t for t in tables if not t.time_fields]
        if no_hours:
            print(f"       ⚠ 未识别到学时字段：{len(no_hours)} 张")
            for t in no_hours[:5]:
                print(f"          · {t.name}")
        if no_time:
            print(f"       ⚠ 未识别到时间字段：{len(no_time)} 张（时间轴将回退到学期标签）")

    if registry.errors:
        print(f"\n  ── 提示 / 错误（{len(registry.errors)} 条）")
        for message in registry.errors[:10]:
            print(f"       · {message[:150]}")

    print("\n[2/4] 校验字段探测抽样 …")
    sample_tables = [
        t
        for t in registry.tables
        if t.hours_fields or t.time_fields
    ][:5]
    for table in sample_tables:
        print(
            f"  {table.name[:24]:<26} 学时={table.hours_fields} "
            f"时间={table.time_fields[:2]} 部组={table.group}"
        )
    if not sample_tables:
        print("  （没有可抽样的表）")

    print("\n[3/4] 隐私回归：确认敏感字段从未进入请求投影 …")
    leaked = [
        (op, table_id, names)
        for op, table_id, names in getattr(client, "projections", [])
        if set(names) & SENSITIVE_FIELDS
    ]
    for table in registry.tables:
        if set(table.safe_fields) & SENSITIVE_FIELDS:
            leaked.append(("registry", table.table_id, tuple(table.safe_fields)))
    print("  OK：无泄漏" if not leaked else f"  ✗ 发现 {len(leaked)} 处泄漏：{leaked[:3]}")

    if args.skip_query:
        print("\n（--skip-query，跳过成员查询）")
        return 0

    print("\n[4/4] 真实成员查询 …")
    for spec in settings.bases:
        print(f"\n  ── {spec.label}")
        sample = pick_sample(client, spec)
        if sample is None:
            print("     取不到样本记录（可能没有权限或表为空）")
            continue
        name, sid = sample
        try:
            result = query_member(
                client, registry, settings.bases, name, sid,
                max_workers=settings.max_workers,
            )
        except FeishuError as exc:
            print(f"     查询失败：{exc}")
            continue

        if not result.found:
            print(f"     {mask(name)} 查询未命中")
            continue

        footprint = summarize(result.participations)
        print(
            f"     {mask(name)}（学号 {sid[:3]}＊＊＊）：命中 "
            f"{footprint.distinct_activity_count} 场活动 / "
            f"{footprint.activity_count} 条记录 / {footprint.total_hours:g} 学时"
        )
        print(f"     跨库合并：{result.member.in_multiple_bases if result.member else False}"
              f"　低置信：{result.low_confidence_count}")
        for bucket in group_breakdown(result.participations)[:6]:
            print(
                f"       {bucket['group']:<10} {bucket['count']:>3} 次  "
                f"{bucket['hours']:>6.1f} 学时"
            )
        if result.errors:
            print(f"     ⚠ {len(result.errors)} 处数据缺失")

    print("\n" + LINE)
    print("自检结束。请人工核对上面的活动表数量与部组归属是否符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
