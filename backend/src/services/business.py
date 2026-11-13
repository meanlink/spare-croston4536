"""Croston/SBA forecast for intermittent parts issues; no inventory mutations."""
from datetime import date, timedelta
import math
import random
import statistics

# 自动选参契约：末 28 天独立检验，候选只在更早的日历上评分。
ENGINE_VERSION = "croston-autoselect-2"
HOLDOUT_DAYS = 28
MIN_HISTORY_DAYS = 45
AUTO_MIN_DAYS = MIN_HISTORY_DAYS + HOLDOUT_DAYS
CANDIDATE_ALPHAS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.8)
CANDIDATE_VARIANTS = ("sba", "croston")
VARIANT_LABELS = {"sba": "SBA 校正", "croston": "原始 Croston"}
MIN_TRAIN_NONZERO = 3
OVERFIT_RATIO = 1.1


def metadata():
    return {
        "name": "稀疏备件领用预测", "short": "spare-croston",
        "tagline": "把零散领用转换成可回测的周度备件协同依据",
        "client": "模拟客户：江湾精机维保组，18 名技师维护 76 台数控机床。",
        "workflow": "技师每晚汇总领用单，计划员在周五用最近七天均值估计下周领用量，再邮件给采购。",
        "pains": ["连续零领用让均值误判需求消失", "一次集中检修拉高短期均值", "预测结果缺少历史回测和参数留痕"],
        "upstream": "ERP 导出的 180 天备件领用日历（包含零需求日）",
        "downstream": "周度维保备料协同会；输出预测用于人工判断，不生成采购单。",
        "entities": ["领用日历", "预测试算", "周度分发计划"],
        "dataset_name": "江湾精机_2026Q3_稀疏领用.csv",
        "columns": [{"key": "day", "label": "领用日", "type": "string"}, {"key": "part", "label": "备件编码", "type": "string"}, {"key": "demand", "label": "领用件数", "type": "number"}],
        "parameters": [
            {"key": "selection", "label": "选参方式", "type": "select", "default": "auto", "options": [{"value": "auto", "label": "自动比较候选（推荐）"}, {"value": "manual", "label": "手动指定 α 与校正（旧口径）"}]},
            {"key": "part", "label": "备件", "type": "select", "default": "R17", "options": [{"value": "R17", "label": "R17 密封环"}, {"value": "G22", "label": "G22 联轴片"}]},
            {"key": "alpha", "label": "平滑系数 α（手选模式生效）", "type": "number", "default": 0.2, "min": 0.05, "max": 0.8},
            {"key": "horizon", "label": "预测天数", "type": "number", "default": 14, "min": 3, "max": 45},
            {"key": "variant", "label": "偏差校正（手选模式生效）", "type": "select", "default": "sba", "options": [{"value": "sba", "label": "SBA 校正"}, {"value": "croston", "label": "原始 Croston"}]},
        ],
        "candidate_alphas": list(CANDIDATE_ALPHAS),
        "candidate_variants": list(CANDIDATE_VARIANTS),
        "holdout_days": HOLDOUT_DAYS,
        "min_auto_days": AUTO_MIN_DAYS,
        "workbench_title": "领用间隔与批量试算", "action_label": "运行领用预测", "unit": "件/天",
        "accent": "oklch(54% 0.12 65)", "style": "parts-ledger",
        "seed_story": ["08:30 上传江湾精机_2026Q3_稀疏领用.csv，共 360 行。", "08:40 校验 R17 与 G22 各 180 天日历，零领用日也保留。", "09:00 选择 R17，由系统在较早日历上自动比较 12 组 α 与校正候选。", "09:10 运行 14 天预测并查看末 28 天独立检验的逐日回测。", "09:20 切换手选口径，比较原始 Croston 与 SBA 的日均预测差异。", "10:00 将累计领用估计提交维保例会，检查停机检修安排。", "16:00 为维保组设置本地收件箱分发计划，归档参数和结果。"],
        "method": f"自动选参：末 {HOLDOUT_DAYS} 天为独立检验期，完全不参与候选评分；在更早的领用日历上用滚动一步 MAE 比较 {len(CANDIDATE_ALPHAS) * len(CANDIDATE_VARIANTS)} 组 α/校正候选，胜出参数再用全历史重训生成未来预测。手选模式沿用原 Croston/SBA 末 {HOLDOUT_DAYS} 天滚动一步回测。",
        "limitations": ["预测是假设未来领用过程稳定时的期望值，不是安全库存或服务水平承诺。", "无采购提前期、库存余额和停机计划输入，不输出补货指令。"],
    }


def seed_records():
    rng = random.Random(1015)
    result = []
    start = date(2026, 4, 1)
    for part, probability in (("R17", 0.20), ("G22", 0.12)):
        for index in range(180):
            amount = rng.choice([1, 1, 2, 3, 5]) if rng.random() < probability else 0
            if index in (72, 144):
                amount += 5 if part == "R17" else 2
            result.append({"day": str(start + timedelta(days=index)), "part": part, "demand": amount})
    return result


def _number(value, name, low, high):
    try:
        answer = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name}必须是数值") from None
    if not math.isfinite(answer) or not low <= answer <= high:
        raise ValueError(f"{name}必须在 {low} 至 {high} 之间")
    return answer


def _rate(values, alpha, sba):
    nonzero = [index for index, value in enumerate(values) if value > 0]
    if not nonzero:
        return 0.0
    first = nonzero[0]
    size = float(values[first])
    gap = float(first + 1)
    last = first
    for index in nonzero[1:]:
        size += alpha * (values[index] - size)
        gap += alpha * ((index - last) - gap)
        last = index
    return size / gap * (1 - alpha / 2 if sba else 1)


def _walk_forward(values, alpha, sba, window):
    """严格向前滚动一步回测：每个预测点只使用该点之前的数据。"""
    window = min(window, len(values) - 1)
    start = len(values) - window
    fitted, baseline = [], []
    for index in range(start, len(values)):
        history = values[:index]
        fitted.append(_rate(history, alpha, sba))
        baseline.append(statistics.mean(history))
    actual = values[start:]
    mae = statistics.mean(abs(a - b) for a, b in zip(actual, fitted))
    baseline_mae = statistics.mean(abs(a - b) for a, b in zip(actual, baseline))
    return actual, fitted, mae, baseline_mae


def _parse_series(records, params):
    if not isinstance(records, list) or not records:
        raise ValueError("请导入包含零需求日的领用日历")
    part = str(params.get("part", "R17"))
    alpha = _number(params.get("alpha", 0.2), "α", 0.05, 0.8)
    horizon_value = _number(params.get("horizon", 14), "预测天数", 3, 45)
    if not horizon_value.is_integer():
        raise ValueError("预测天数必须是整数")
    horizon = int(horizon_value)
    variant = params.get("variant", "sba")
    if variant not in ("sba", "croston"):
        raise ValueError("未知的 Croston 变体")
    # 旧任务参数没有 selection 键，按手选旧口径处理，保证历史结果可复现。
    selection = params.get("selection", "manual")
    if selection not in ("auto", "manual"):
        raise ValueError("未知的选参方式")
    rows = []
    for record in records:
        if str(record.get("part")) != part:
            continue
        try:
            day = date.fromisoformat(str(record["day"]))
            demand = float(record["demand"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("领用日须为 ISO 日期且 demand 为非负数") from None
        if not math.isfinite(demand) or not 0 <= demand <= 1_000_000_000:
            raise ValueError("单日领用量必须是0至10亿之间的有限数值")
        rows.append((day, demand))
    rows.sort()
    if len(rows) < MIN_HISTORY_DAYS:
        raise ValueError("所选备件至少需要 45 个连续自然日")
    if any((rows[i][0] - rows[i - 1][0]).days != 1 for i in range(1, len(rows))):
        raise ValueError("领用日历有重复或缺失日期，请补全零需求日")
    return part, alpha, horizon, variant, selection, rows


def _forecast_table(rows, horizon, rate):
    if rows[-1][0].toordinal() + horizon > date.max.toordinal():
        raise ValueError("预测末日超出日历支持范围，请检查最后观测日期")
    last_day = rows[-1][0]
    future = [
        {"day": str(last_day + timedelta(days=i)), "expected": round(rate, 4), "cumulative": round(rate * i, 3)}
        for i in range(1, horizon + 1)
    ]
    return {"columns": [{"key": "day", "label": "预测日期"}, {"key": "expected", "label": "当日领用期望"}, {"key": "cumulative", "label": "累计领用期望"}], "rows": future}


def _assemble(part, rows, horizon, rate, alpha, variant, actual, fitted, mae, baseline_mae,
              mae_label, summary, findings, warnings, selection_block):
    values = [item[1] for item in rows]
    nonzero_count = sum(value > 0 for value in values)
    final_warnings = list(warnings)
    if nonzero_count < 5:
        final_warnings.append("非零领用日少于 5 个，预测对单次领用非常敏感。")
    if mae > baseline_mae:
        final_warnings.append("本次末 28 天回测 MAE 高于历史均值基线，应保留人工复核。")
    final_findings = list(findings)
    final_findings.append("领用期望可以是小数；需要整数备料时应在例会结合检修计划另行决策。")
    return {
        "engine": ENGINE_VERSION,
        "summary": summary,
        "metrics": [
            {"label": "平滑领用率", "value": round(rate, 4), "unit": "件/天"},
            {"label": "预测期累计", "value": round(rate * horizon, 2), "unit": "件"},
            {"label": mae_label, "value": round(mae, 4), "unit": "件"},
            {"label": "非零日占比", "value": round(nonzero_count / len(values) * 100, 2), "unit": "%"},
        ],
        "table": _forecast_table(rows, horizon, rate),
        "chart": {
            "type": "line",
            "labels": [str(row[0]) for row in rows[-HOLDOUT_DAYS:]],
            "series": [
                {"name": "真实领用", "values": actual},
                {"name": "滚动一步预测", "values": [round(value, 4) for value in fitted]},
            ],
        },
        "findings": final_findings,
        "method": metadata()["method"],
        "warnings": final_warnings,
        "selection": selection_block,
    }


def _analyze_manual(part, rows, alpha, horizon, variant):
    values = [item[1] for item in rows]
    sba = variant == "sba"
    actual, fitted, mae, baseline_mae = _walk_forward(values, alpha, sba, HOLDOUT_DAYS)
    rate = _rate(values, alpha, sba)
    nonzero_count = sum(value > 0 for value in values)
    selection_block = {
        "mode": "manual",
        "alpha": alpha,
        "variant": variant,
        "variant_label": VARIANT_LABELS[variant],
        "label": "手动指定 α 与偏差校正，未执行候选自动比较；末 28 天回测仅用于呈现该组参数的表现。",
    }
    summary = f"{part} 在 {len(rows)} 天中有 {nonzero_count} 个领用日；{variant.upper()} 预计未来 {horizon} 天领用 {rate * horizon:.2f} 件。"
    findings = [
        f"历史均值基线 MAE={baseline_mae:.4f}；本次模型 MAE={mae:.4f}，回测每个预测仅使用前一天及更早数据。",
        f"α={alpha:g}；最后观测日 {rows[-1][0]}；未来 {horizon} 天按同一稳定领用率外推。",
    ]
    return _assemble(part, rows, horizon, rate, alpha, variant, actual, fitted, mae, baseline_mae,
                     "末28天 MAE", summary, findings, [], selection_block)


def _split_block(rows, split):
    train_start, train_end = rows[0][0], rows[split - 1][0]
    holdout_start, holdout_end = rows[split][0], rows[-1][0]
    return {
        "mode": "auto",
        "candidate_count": len(CANDIDATE_ALPHAS) * len(CANDIDATE_VARIANTS),
        "candidate_alphas": list(CANDIDATE_ALPHAS),
        "candidate_variants": [{"value": v, "label": VARIANT_LABELS[v]} for v in CANDIDATE_VARIANTS],
        "train_start": str(train_start),
        "train_end": str(train_end),
        "train_days": split,
        "holdout_start": str(holdout_start),
        "holdout_end": str(holdout_end),
        "holdout_days": HOLDOUT_DAYS,
        "selection_window_days": HOLDOUT_DAYS,
        "split_rule": f"末 {HOLDOUT_DAYS} 个自然日（{holdout_start} 至 {holdout_end}）为独立检验期，只做事后核验、不参与任何候选评分；{train_start} 至 {train_end} 共 {split} 天为选参段，候选打分只读取这段较早日历。",
        "tie_break_rule": "选参滚动 MAE 相同则取较小 α；α 也相同则 SBA 优先于原始 Croston，保证同一份数据结果可复现。",
    }


def _analyze_auto(part, rows, horizon):
    values = [item[1] for item in rows]
    if len(rows) < AUTO_MIN_DAYS:
        raise ValueError(
            f"自动选参需至少 {AUTO_MIN_DAYS} 个连续自然日（{MIN_HISTORY_DAYS} 天选参段 + 末 {HOLDOUT_DAYS} 天独立检验期），当前仅 {len(rows)} 天；可改用手选模式（至少 {MIN_HISTORY_DAYS} 天）。"
        )
    split = len(rows) - HOLDOUT_DAYS
    train = values[:split]
    train_nonzero = sum(value > 0 for value in train)
    all_nonzero = sum(value > 0 for value in values)
    block = _split_block(rows, split)
    block["train_nonzero_days"] = train_nonzero

    # 全零或选参段全零：没有可学习的非零事件，候选无法评分，明确返回零预测而不是制造虚假排名。
    if all_nonzero == 0 or train_nonzero == 0:
        fallback_alpha, fallback_variant = CANDIDATE_ALPHAS[0], "sba"
        actual, fitted, mae, baseline_mae = _walk_forward(values, fallback_alpha, True, HOLDOUT_DAYS)
        if all_nonzero == 0:
            reason = "全历史领用量均为 0，Croston 没有任何非零领用可学习，12 组候选全部输出零预测、不存在可比较的得分；按零需求给出预测并标记为证据不足，请补充含真实领用的日历后重新试算。"
        else:
            reason = f"选参段（{block['train_start']} 至 {block['train_end']}）全部为零领用，候选在该段上没有可学习的非零事件、无法评分；检验期虽出现领用，也不能用来回头选参。按零需求给出预测，待新数据进入选参段后重新试算。"
        block.update({
            "status": "insufficient_evidence",
            "candidates": [],
            "winner": None,
            "winner_reason": reason,
            "applied": {"alpha": fallback_alpha, "variant": fallback_variant,
                        "variant_label": VARIANT_LABELS[fallback_variant], "rate": 0.0},
        })
        summary = f"{part} 在 {len(rows)} 天中无{'任何' if all_nonzero == 0 else '选参段内'}非零领用，证据不足，未来 {horizon} 天领用期望按 0 件处理。"
        findings = [block["split_rule"], reason]
        warnings = ["自动选参证据不足，未来预测全部为 0；该结果不能视为需求消失的结论，请保留人工复核。"]
        return _assemble(part, rows, horizon, 0.0, fallback_alpha, fallback_variant, actual, fitted,
                         mae, baseline_mae, "独立检验 MAE（末28天）", summary, findings, warnings, block)

    # 候选打分：只在选参段（较早日历）内做滚动一步 MAE。
    candidates = []
    for variant in CANDIDATE_VARIANTS:
        sba = variant == "sba"
        for alpha in CANDIDATE_ALPHAS:
            _, _, selection_mae, selection_baseline = _walk_forward(train, alpha, sba, HOLDOUT_DAYS)
            candidates.append({
                "alpha": alpha,
                "variant": variant,
                "variant_label": VARIANT_LABELS[variant],
                "selection_mae": round(selection_mae, 4),
                "selection_baseline_mae": round(selection_baseline, 4),
            })
    ranked = sorted(candidates, key=lambda c: (c["selection_mae"], c["alpha"], 0 if c["variant"] == "sba" else 1))
    for rank, candidate in enumerate(ranked, start=1):
        candidate["rank"] = rank
    winner = ranked[0]
    runner_up = ranked[1]

    # 独立检验期评分：逐日只用此前数据，供事后核验，不回写选择。
    for candidate in ranked:
        _, _, holdout_mae, holdout_baseline = _walk_forward(
            values, candidate["alpha"], candidate["variant"] == "sba", HOLDOUT_DAYS
        )
        candidate["holdout_mae"] = round(holdout_mae, 4)
        candidate["holdout_baseline_mae"] = round(holdout_baseline, 4)

    w_alpha, w_variant = winner["alpha"], winner["variant"]
    rate = _rate(values, w_alpha, w_variant == "sba")
    actual, fitted, mae, baseline_mae = _walk_forward(values, w_alpha, w_variant == "sba", HOLDOUT_DAYS)

    winner_reason = (
        f"仅基于选参段 {block['train_start']} 至 {block['train_end']} 的 {HOLDOUT_DAYS} 天滚动一步回测，"
        f"{VARIANT_LABELS[w_variant]} α={w_alpha:g} 的 MAE={winner['selection_mae']:.4f} 为全部候选最低；"
        f"次优为 {VARIANT_LABELS[runner_up['variant']]} α={runner_up['alpha']:g}，MAE={runner_up['selection_mae']:.4f}。"
        f"独立检验期 MAE={winner['holdout_mae']:.4f} 仅做事后核验，未参与选择。"
    )
    block.update({
        "status": "ok" if train_nonzero >= MIN_TRAIN_NONZERO else "sparse",
        "candidates": ranked,
        "winner": {
            "alpha": w_alpha,
            "variant": w_variant,
            "variant_label": VARIANT_LABELS[w_variant],
            "selection_mae": winner["selection_mae"],
            "holdout_mae": winner["holdout_mae"],
        },
        "winner_reason": winner_reason,
        "applied": {"alpha": w_alpha, "variant": w_variant,
                    "variant_label": VARIANT_LABELS[w_variant], "rate": round(rate, 4)},
    })

    warnings = []
    if train_nonzero < MIN_TRAIN_NONZERO:
        warnings.append(
            f"选参段仅 {train_nonzero} 个非零领用日（少于 {MIN_TRAIN_NONZERO}），候选 MAE 对个别领用事件高度敏感，胜出参数置信度低，建议人工复核。"
        )
    if winner["selection_mae"] > 0 and winner["holdout_mae"] > winner["selection_mae"] * OVERFIT_RATIO:
        warnings.append(
            f"胜出方案独立检验期 MAE（{winner['holdout_mae']:.4f}）高于选参窗口 MAE（{winner['selection_mae']:.4f}）的 1.1 倍，选参效果可能被高估，请结合检验曲线人工判断。"
        )

    summary = (
        f"{part} 在 {len(rows)} 天中有 {all_nonzero} 个领用日；自动选参胜出 {VARIANT_LABELS[w_variant]} α={w_alpha:g}，"
        f"预计未来 {horizon} 天领用 {rate * horizon:.2f} 件。"
    )
    findings = [
        block["split_rule"],
        f"候选共 {block['candidate_count']} 组（α∈{list(CANDIDATE_ALPHAS)} × SBA/原始 Croston）；选分只读取选参段的 {HOLDOUT_DAYS} 天滚动一步 MAE，检验期对每个候选逐日仅用此前数据评分但不影响排名。",
        winner_reason + block["tie_break_rule"],
        f"胜出参数已用全部 {len(rows)} 天历史重训，平滑领用率 {rate:.4f} 件/天；最后观测日 {rows[-1][0]}，未来 {horizon} 天按同一稳定领用率外推。",
    ]
    # 自动模式多展示一个选参窗口得分指标，便于和独立检验 MAE 对照。
    result = _assemble(part, rows, horizon, rate, w_alpha, w_variant, actual, fitted, mae, baseline_mae,
                       "独立检验 MAE（末28天）", summary, findings, warnings, block)
    result["metrics"].insert(3, {"label": "选参窗口 MAE（胜出方案）", "value": winner["selection_mae"], "unit": "件"})
    return result


def analyze(records, params):
    part, alpha, horizon, variant, selection, rows = _parse_series(records, params)
    if selection == "manual":
        return _analyze_manual(part, rows, alpha, horizon, variant)
    return _analyze_auto(part, rows, horizon)
