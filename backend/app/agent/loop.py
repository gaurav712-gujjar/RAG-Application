"""Agent mode: the model decides which tools to call (documents, web, calculator), possibly several
times, then writes the final answer. Bounded by `agent_max_steps`; a failure falls back to answering
from what was already gathered."""
import json
import logging
from collections.abc import AsyncIterator

from app.agent.tools import LABELS, TOOL_SPECS, AgentToolbox
from app.core.config import Settings
from app.llm.groq_client import LLMUnavailable
from app.llm.prompts import answer_language

logger = logging.getLogger(__name__)

MAX_CALLS_PER_STEP = 3

SYSTEM = (
    "You are DocChat's research agent. You can call tools to find information, then you write the answer.\n"
    "Rules:\n"
    "- For anything that may be in the user's files, call search_documents. Search by TOPIC (for example "
    "'refund policy', 'shipping charges'), never by file name.\n"
    "- For a comparison or a question with several parts, make one search_documents call per part or per "
    "document, then compare what each returned.\n"
    "- Never assume a rate, fee, limit or date. Look it up with search_documents before you use it in a "
    "calculation, and cite it as [n]. State any condition that applies (for example 'only for opened electronics').\n"
    "- Use calculator for every calculation. Never do arithmetic yourself. If the question is ambiguous "
    "(for example whether an amount is per item or in total), pick the most natural reading and say which one you used.\n"
    "- Use search_web only for public or current information, or when the documents do not have the answer.\n"
    "- Do not call the same tool with the same input twice. Stop calling tools once you have enough.\n"
    "- Base facts only on tool results. Cite them as [n] using the numbers the tools returned. "
    "If the tools found nothing relevant, say so plainly. Do not fill a table cell with '-' when the "
    "tool results contain the answer; search again instead.\n"
    "- Be concise. Markdown (short lists, bold, tables) is fine for multi-part answers. "
    "Never write HTML tags such as <br>; inside a table cell keep one short phrase, or use a list below the table.\n"
    "- Reply in {language}."
)


class AgentRun:
    """Holds the numbers the caller needs even if the client disconnects mid-run."""

    def __init__(self):
        self.tokens = 0
        self.steps = 0
        self.model = ""
        self.sources: list[dict] = []

    def add(self, turn) -> None:
        self.tokens += (turn.prompt_tokens or 0) + (turn.completion_tokens or 0)


async def run_agent(
    *, run: AgentRun, llm, state, settings: Settings, user_id: str, question: str, history: list[dict],
    doc_ids: list[str] | None,
) -> AsyncIterator[dict]:
    """Yields {"type": "tool"|"sources"|"final", ...} events."""
    model = settings.agent_model or settings.llm_model_strong
    run.model = model
    box = AgentToolbox(user_id=user_id, state=state, settings=settings, doc_ids=doc_ids)
    messages: list[dict] = [{"role": "system", "content": SYSTEM.format(language=answer_language(question))}]
    messages += [{"role": t["role"], "content": t["content"]} for t in history]
    messages.append({"role": "user", "content": question})
    seen_calls: set[tuple[str, str]] = set()
    max_out = settings.agent_max_output_tokens

    final_text = ""
    for _ in range(max(1, settings.agent_max_steps)):
        try:
            turn = await llm.complete_tools(messages, model, TOOL_SPECS, max_out, settings.temperature)
        except LLMUnavailable:
            raise
        except Exception:
            logger.exception("agent tool turn failed; answering from what was gathered")
            break  # e.g. the model produced a malformed tool call
        run.add(turn)
        if not turn.tool_calls:
            final_text = turn.text
            break
        messages.append({
            "role": "assistant", "content": turn.text or None,
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in turn.tool_calls
            ],
        })
        for i, call in enumerate(turn.tool_calls):
            try:
                args = json.loads(call.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            key = (call.name, json.dumps(args, sort_keys=True))
            detail = str(args.get("query") or args.get("expression") or "")
            if i >= MAX_CALLS_PER_STEP:
                result = "Skipped: too many tool calls at once."
            elif key in seen_calls:
                result = "You already ran this exact call. Use the earlier result."
            else:
                seen_calls.add(key)
                run.steps += 1
                yield {"type": "tool", "name": call.name, "label": LABELS.get(call.name, call.name), "detail": detail}
                result = await box.run(call.name, args)
                run.sources = box.sources
                yield {"type": "sources", "sources": box.sources}
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    if not final_text:  # step limit reached or a tool turn failed: write the answer without tools
        messages.append({"role": "user", "content": "Now write the final answer from the tool results above. Do not call tools."})
        turn = await llm.complete_tools(messages, model, None, max_out, settings.temperature)
        run.add(turn)
        final_text = turn.text
    yield {"type": "final", "text": final_text or "I could not produce an answer. Please try again."}
