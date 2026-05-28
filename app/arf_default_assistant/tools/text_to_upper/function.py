"""text_to_upper -- Convert input text to uppercase."""


async def execute(text: str) -> dict:
    """Convert the input text to uppercase and return the result."""
    try:
        result = text.upper()
        return {
            "ok": True,
            "original": text,
            "result": result,
            "length": len(result),
        }
    except Exception as e:
        return {"error": str(e)}
