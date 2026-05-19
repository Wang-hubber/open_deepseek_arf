"""Task complexity classifier for automatic model routing.

Analyzes the user's request and routes to the appropriate model tier:
  medium  -> quick_thinking (reasoning enabled)
  complex -> deep_thinking (maximum reasoning)

quick_no_thinking is reserved for background tasks (compression, summaries).
"""

import logging

logger = logging.getLogger("arf.classifier")

CLASSIFY_SYSTEM_PROMPT = """You are a task complexity classifier. Analyze the user's message and respond with exactly one word: medium or complex.

Rules:
- medium: Greetings, simple Q&A, factual lookups, file reads, single edits, code generation, debugging, tool orchestration, resource creation, data processing
- complex: System design, multi-file refactoring, architectural decisions, creative writing, tasks requiring sustained deep thinking, security analysis

Examples:
"hello" -> medium
"what files are in my workspace" -> medium
"write a python script that downloads a webpage" -> medium
"find the bug in this function and fix it" -> medium
"design a microservice architecture for an e-commerce platform" -> complex
"refactor the auth module to use OAuth2 with JWT rotation" -> complex

User message: {message}

Classification:"""

CLASSIFICATION_TO_MODEL = {
    "medium": "quick_thinking",
    "complex": "deep_thinking",
}

# Degradation chain when target model type is unavailable.
# Falls back to the next-less-capable configured model type.
DEGRADATION = {
    "deep_thinking": ["quick_thinking", "quick_no_thinking"],
    "quick_thinking": ["quick_no_thinking"],
}


def classify_request(classifier_call, messages: list[dict]) -> str:
    """Classify the complexity of the latest user message.

    Args:
        classifier_call: A callable that takes a list of message dicts
                         and returns a string (the model's text response).
        messages: The full conversation messages list.

    Returns:
        "medium" or "complex". Defaults to "medium" on error.
    """
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                last_user = content[:2000]
            elif isinstance(content, list):
                # Multimodal content -- extract text parts
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                last_user = " ".join(parts)[:2000]
            break

    if not last_user:
        return "medium"

    prompt = [
        {"role": "user", "content": CLASSIFY_SYSTEM_PROMPT.format(message=last_user)},
    ]

    try:
        result = classifier_call(prompt)
        result = result.strip().lower().rstrip(".")
        if result in ("medium", "complex"):
            return result
        logger.debug("Classifier returned unrecognized value: %r, defaulting to medium", result)
        return "medium"
    except Exception as e:
        logger.warning("Classifier call failed: %s, defaulting to medium", e)
        return "medium"


def resolve_model_for_classification(
    classification: str,
    available_types: set[str],
) -> str:
    """Resolve a model type for the given classification, with degradation.

    Args:
        classification: "simple", "medium", or "complex"
        available_types: Set of configured model types

    Returns:
        The resolved model type string.
    """
    target = CLASSIFICATION_TO_MODEL.get(classification, "quick_thinking")

    if target in available_types:
        return target

    # Degrade
    chain = DEGRADATION.get(target, [])
    for fallback in chain:
        if fallback in available_types:
            logger.info(
                "Model type %r not available, degraded to %r",
                target, fallback,
            )
            return fallback

    # Last resort: return any available type
    if available_types:
        return next(iter(available_types))

    logger.warning("No model types available for classification %r", classification)
    return target
