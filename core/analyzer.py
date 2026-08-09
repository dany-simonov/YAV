"""Hybrid text analyzer: AI detection (Sapling) + fact-check via g4f with cascade fallback."""

import asyncio
import json
import time
from typing import Any, Dict, List

import g4f

from adapters.sapling import SaplingAdapter
from core.enums import ModelUsed
from src.validation import (
    MAX_FACT_CHECK_ITEMS,
    bounded_provider_string,
    safe_external_url,
)

# Strict system prompt for web-enabled fact-checking
FACTCHECK_SYSTEM_PROMPT = (
    "Ты — профессиональный фактчекер с доступом к веб-поиску. "
    "Проверяй утверждения, находи первоисточники, используй свежие данные. "
    "Отвечай СТРОГО валидным JSON без каких-либо обёрток, markdown, комментариев. "
    "Структура ответа: {\"fact_checks\": [{\"exact_quote\": \"точный фрагмент\", "
    "\"status\": \"fake\"|\"manipulation\"|\"plagiarism\"|\"ok\", "
    "\"truth\": \"корректный факт или объяснение\", \"source_url\": \"https://...\"}]} "
    "Не добавляй лишних полей. \"exact_quote\" должен быть точной подстрокой исходного текста, "
    "по возможности минимальной длины (слово или короткая фраза), чтобы подсветка была пословной."
)


class HybridTextAnalyzer:
    """Runs AI-detection (Sapling) and fact-check (g4f) in parallel, merges highlights."""

    MODEL_CASCADE = [
        "gpt-4.1-nano",  # primary
        "gpt-oss-120b",  # fallback 1
        "command-r",     # fallback 2
    ]

    FACTCHECK_TIMEOUT_S = 12

    def __init__(self) -> None:
        self.sapling = SaplingAdapter()

    async def _call_g4f(self, model_name: str, text: str) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": FACTCHECK_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]

        def _run():
            return g4f.ChatCompletion.create(
                model=model_name,
                messages=messages,
                timeout=self.FACTCHECK_TIMEOUT_S,
            )

        raw = await asyncio.to_thread(_run)
        content = "" if raw is None else ("".join(raw) if not isinstance(raw, str) else raw)
        ok, parsed = self._parse_json(content)
        if not ok or not isinstance(parsed, dict) or "fact_checks" not in parsed:
            raise ValueError("Invalid JSON from g4f")
        return parsed

    @staticmethod
    def _parse_json(text: str) -> tuple[bool, Any]:
        if not text:
            return False, None
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        try:
            return True, json.loads(cleaned)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    return True, json.loads(text[start:end])
                except json.JSONDecodeError:
                    return False, None
            return False, None

    async def fact_check(self, text: str) -> tuple[Dict[str, Any], str]:
        """Run g4f with cascade fallback; returns (parsed_json, model_name)."""
        last_error = ""
        for model in self.MODEL_CASCADE:
            try:
                parsed = await self._call_g4f(model, text)
                return parsed, model
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                continue
        raise RuntimeError(f"All g4f models failed: {last_error}")

    @staticmethod
    def merge_results(text: str, fact_checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Project fact-check spans onto original text, producing token list."""
        spans: list[tuple[int, int, Dict[str, Any]]] = []
        lower_text = text
        used_positions: set[int] = set()

        for fc in fact_checks:
            quote = (fc.get("exact_quote") or "").strip()
            status = (fc.get("status") or "").lower()
            if not quote:
                continue
            start = lower_text.find(quote)
            if start == -1 or start in used_positions:
                continue
            end = start + len(quote)
            used_positions.add(start)
            spans.append((start, end, fc))

        spans.sort(key=lambda x: x[0])
        tokens: list[Dict[str, Any]] = []
        cursor = 0
        for start, end, fc in spans:
            if start > cursor:
                tokens.append({"text": text[cursor:start], "type": "normal"})
            status = (fc.get("status") or "").lower()
            if status == "fake":
                token_type = "fake"
            elif status == "plagiarism":
                token_type = "plagiarism"
            elif status == "ok":
                token_type = "normal"
            else:
                token_type = "manipulation"

            tokens.append(
                {
                    "text": text[start:end],
                    "type": token_type,
                    "details": {
                        "truth": fc.get("truth", ""),
                        "source_url": fc.get("source_url", ""),
                    },
                }
            )
            cursor = end
        if cursor < len(text):
            tokens.append({"text": text[cursor:], "type": "normal"})
        return tokens

    async def analyze(self, text: str) -> Dict[str, Any]:
        start_ts = time.monotonic()

        sapling_task = asyncio.create_task(self.sapling.analyze(text.encode("utf-8")))

        fc_parsed: Dict[str, Any] = {"fact_checks": []}
        fc_model = "g4f_timeout"
        try:
            fc_parsed, fc_model = await asyncio.wait_for(
                self.fact_check(text),
                timeout=self.FACTCHECK_TIMEOUT_S,
            )
        except Exception:
            fc_parsed = {"fact_checks": []}
            fc_model = "g4f_unavailable"

        sapling_res = await sapling_task

        raw_checks = fc_parsed.get("fact_checks", []) if isinstance(fc_parsed, dict) else []
        if not isinstance(raw_checks, list):
            raw_checks = []
        fact_checks = []
        for item in raw_checks[:MAX_FACT_CHECK_ITEMS]:
            if not isinstance(item, dict):
                continue
            quote = bounded_provider_string(item.get("exact_quote"), 500)
            truth = bounded_provider_string(item.get("truth"), 2_000)
            status = bounded_provider_string(item.get("status"), 32).lower()
            if not quote or status not in {"fake", "manipulation", "plagiarism", "ok"}:
                continue
            source_url = safe_external_url(item.get("source_url") or item.get("source"))
            fact_checks.append(
                {
                    "exact_quote": quote,
                    "truth": truth,
                    "status": status,
                    "source_url": source_url,
                }
            )
        tokens = self.merge_results(text, fact_checks)

        verdict = (
            "contains_fakes"
            if any(t.get("type") in {"fake", "manipulation", "plagiarism"} for t in tokens)
            else "clean"
        )

        result = {
            "verdict": verdict,
            "ai_confidence": sapling_res.confidence,
            "ai_verdict": sapling_res.verdict.value,
            "fact_checks": fact_checks,
            "tokens": tokens,
            "model_used": fc_model,
            "processing_ms": int((time.monotonic() - start_ts) * 1000),
            "model_used_enum": ModelUsed.HYBRID_G4F,
        }
        if fc_model in {"g4f_timeout", "g4f_unavailable"}:
            result["factcheck_error"] = fc_model

        return result
