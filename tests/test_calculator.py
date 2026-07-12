"""Calculator uses a restricted AST evaluator, never eval() (engineering-spec §8.1)."""

import pytest

from agent_core.tools.base import ExecutionContext
from agent_core.tools.calculator import CalculatorTool, safe_eval


@pytest.mark.parametrize(
    "expr,expected",
    [("2 + 3", 5), ("(120 * 1.21)", 145.2), ("2 ** 10", 1024), ("-4 + 6", 2)],
)
def test_safe_eval_arithmetic(expr, expected):
    assert safe_eval(expr) == pytest.approx(expected)


def test_safe_eval_rejects_code():
    # No names, calls, or attribute access — arbitrary code is a hard non-goal.
    for hostile in ("__import__('os')", "open('x')", "x + 1"):
        with pytest.raises(ValueError):
            safe_eval(hostile)


def test_execute_returns_failure_not_exception_on_bad_input():
    result = CalculatorTool().execute({"expression": "1/0"}, ExecutionContext(conversation_id="t"))
    assert result.success is False
    assert "Could not calculate" in result.content
