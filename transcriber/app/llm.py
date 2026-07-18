"""LiteLLM wrapper for transcript analysis."""

import os
from typing import AsyncGenerator

import litellm

from .prompts import TASKS, get_task_prompt

# Configuration from environment
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "anthropic/claude-opus-4.8")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

# Allowed models (validated server-side)
ALLOWED_MODELS = [
    "anthropic/claude-opus-4.8",
    "anthropic/claude-opus-4.8-fast",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-haiku-4.5",
    "google/gemini-3.5-flash",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.1-flash-lite",
    "qwen/qwen3.7-max",
    "minimax/minimax-m3",
    "moonshotai/kimi-k2.6",
    "openai/gpt-5.5-pro",
    "openai/gpt-5.5",
]


def get_allowed_models() -> list[dict]:
    """Return list of allowed models with display names."""
    return [
        {"id": m, "name": m.split("/")[-1].replace("-", " ").title()}
        for m in ALLOWED_MODELS
    ]


def is_valid_model(model: str) -> bool:
    """Check if model is in allowed list."""
    return model in ALLOWED_MODELS

# Configure LiteLLM
litellm.set_verbose = False


def _get_model_config(model: str | None = None) -> dict:
    """Get model configuration for LiteLLM."""
    model_id = model or DEFAULT_MODEL
    
    # If using OpenRouter, prefix model with 'openrouter/'
    if OPENAI_BASE_URL and "openrouter.ai" in OPENAI_BASE_URL:
        if not model_id.startswith("openrouter/"):
            model_id = f"openrouter/{model_id}"
    
    config = {"model": model_id}
    
    # If using custom base URL (OpenRouter), configure it
    if OPENAI_BASE_URL:
        config["api_base"] = OPENAI_BASE_URL
    if OPENAI_API_KEY:
        config["api_key"] = OPENAI_API_KEY
        
    return config


LANGUAGE_NAMES = {
    "en": "English",
    "de": "German",
}


async def analyze_transcript(
    transcript: str,
    task_id: str,
    output_language: str | None = None,
    model: str | None = None,
    custom_prompt: str | None = None,
) -> str:
    """
    Analyze transcript with LLM for a specific task.
    
    Args:
        transcript: The transcript text to analyze
        task_id: Task identifier (improve, summary, keypoints, concepts, tasks)
        output_language: Target language code ('en', 'de') or None for source language
        model: Model ID to use (must be in ALLOWED_MODELS)
        custom_prompt: Custom prompt to use instead of built-in
        
    Returns:
        Analysis result as markdown text
        
    Raises:
        ValueError: If task_id is invalid or model not allowed
        Exception: If LLM call fails
    """
    if model and not is_valid_model(model):
        raise ValueError(f"Model not allowed: {model}")
    
    prompt = get_task_prompt(task_id, custom_prompt)
    if not prompt:
        raise ValueError(f"Unknown task: {task_id}")
    
    # Add language instruction
    if output_language and output_language in LANGUAGE_NAMES:
        lang_name = LANGUAGE_NAMES[output_language]
        prompt = f"{prompt}\n\nIMPORTANT: You MUST write your entire response in {lang_name}."
    else:
        # Default: match source language
        prompt = f"{prompt}\n\nIMPORTANT: You MUST write your response in the SAME LANGUAGE as the transcript. If the transcript is in German, respond in German. If in English, respond in English."
    
    config = _get_model_config(model)
    
    response = await litellm.acompletion(
        **config,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": transcript},
        ],
        temperature=0.3,  # Lower temperature for more consistent output
        max_tokens=4096,
    )
    
    return response.choices[0].message.content


async def analyze_transcript_streaming(
    transcript: str,
    task_id: str,
) -> AsyncGenerator[str, None]:
    """
    Analyze transcript with streaming response.
    
    Yields:
        Chunks of the analysis result
    """
    prompt = get_task_prompt(task_id)
    if not prompt:
        raise ValueError(f"Unknown task: {task_id}")
    
    config = _get_model_config()
    
    response = await litellm.acompletion(
        **config,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": transcript},
        ],
        temperature=0.3,
        max_tokens=4096,
        stream=True,
    )
    
    async for chunk in response:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


async def analyze_multiple_tasks(
    transcript: str,
    task_ids: list[str],
    output_language: str | None = None,
    model: str | None = None,
    on_task_complete: callable = None,
) -> dict[str, str]:
    """
    Run multiple analysis tasks on a transcript.
    
    Args:
        transcript: The transcript text
        task_ids: List of task IDs to run
        output_language: Target language code or None for source language
        model: Model ID to use
        on_task_complete: Optional callback(task_id, result) called after each task
        
    Returns:
        Dict mapping task_id to result
    """
    results = {}
    
    for task_id in task_ids:
        if task_id not in TASKS:
            continue
            
        try:
            result = await analyze_transcript(transcript, task_id, output_language, model)
            results[task_id] = result
            
            if on_task_complete:
                on_task_complete(task_id, result)
        except Exception as e:
            results[task_id] = f"Error: {str(e)}"
            
    return results
