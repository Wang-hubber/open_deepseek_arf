"""Evaluation-specific exceptions."""


class EvalError(Exception):
    """Raised when eval operations encounter an unrecoverable error."""
    pass


class EvalJudgeError(EvalError):
    """Raised when the judge model API call fails — fatal, should abort the run."""
    pass
