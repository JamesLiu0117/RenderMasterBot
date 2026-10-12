"""Exact, auditable shortcuts; free-form or modified requests still use the model."""

from render_master_bot.contracts import ConversationRouteIntent


PROFILING_PROMPT = """Run a profiling-first audit of the current level using the active PIE/SIE workload.

Collect runtime frame-time samples and Unreal Insights GPU scope traces.
Run repeatable Actor impact studies for eligible candidates.

Report measured frame-time statistics, leading GPU scopes, stable findings,
and any skipped measurements or limitations.

Do not apply optimization changes. Base performance conclusions on profiler
evidence, not screenshots or static complexity estimates."""


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split()).rstrip(".?!。？！")


def explicit_conversation_decision(text: str) -> ConversationRouteIntent | None:
    """Match whole utterances, never action keywords in questions or negations."""
    key = _normalized(text)
    capability_questions = {
        "what can you do", "what are your capabilities", "what can you help me with",
        "你能做什么", "你都可以做什么", "你可以做什么", "你能帮我做什么",
    }
    if key in capability_questions:
        chinese = any("\u4e00" <= char <= "\u9fff" for char in text)
        message = (
            "我可以读取当前 Unreal 上下文、查找材质、准备 Actor 变换、灯光和相机修改，"
            "以及执行关卡审计、运行时采样和 Unreal Insights GPU 分析。"
            "正式场景修改需要你审核批准；性能分析需要先启动有代表性的 PIE/SIE。"
            "我会区分实测结果与推测，并报告无法测量的部分。你想先检查什么？"
            if chinese else
            "I can inspect Unreal context, find materials, prepare Actor Transform, light "
            "and camera changes, and run level audits, runtime sampling and Unreal Insights "
            "GPU analysis. Scene changes require your review and approval. Profiling needs "
            "a representative PIE/SIE workload. I distinguish measurements from hypotheses "
            "and report missing evidence. What would you like to inspect?"
        )
        return ConversationRouteIntent(
            outcome="respond", intent="answer_question", assistant_message=message,
            action_prompt=None, clarification_question=None, required_context=[],
        )
    profiling_commands = {
        _normalized(PROFILING_PROMPT),
        "run a profiling-first level audit", "run profiling-first level audit",
        "profile the current level", "run a profiling-first audit of the current level",
        "对当前关卡进行性能分析", "分析当前关卡的性能", "运行当前关卡的性能分析",
    }
    if key in profiling_commands:
        return ConversationRouteIntent(
            outcome="route", intent="level_profiling",
            assistant_message=(
                "I can run the profiling-first level audit and report measured results, "
                "skipped measurements and limitations. No optimization will be applied."
            ),
            action_prompt=text,
            clarification_question=None, required_context=[],
        )
    return None
