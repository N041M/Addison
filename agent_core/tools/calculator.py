"""Calculator / unit conversion tool — LOW risk, no external effect (design-doc §7.4.1).

Fully implemented as the reference LOW-risk tool (engineering-spec §11 step 2).
Uses a restricted AST evaluator, NOT ``eval`` — arbitrary code execution is a
hard non-goal (engineering-spec §8.1).
"""

from __future__ import annotations

import ast
import operator

from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("Unsupported expression.")


def safe_eval(expression: str) -> float:
    return _eval_node(ast.parse(expression, mode="eval"))


class CalculatorTool:
    definition = ToolDefinition(
        id="calculator",
        label="Do math and unit conversions",
        description="Adds, subtracts, and converts numbers. Never touches your files or the internet.",
        risk_tier=RiskTier.LOW,
        parameters_schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "An arithmetic expression, e.g. '(120 * 1.21)'.",
                }
            },
            "required": ["expression"],
        },
    )

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        try:
            value = safe_eval(str(args["expression"]))
            return ToolResult(success=True, content=value)
        except (ValueError, SyntaxError, KeyError, ZeroDivisionError) as exc:
            return ToolResult(success=False, content=f"Could not calculate that: {exc}")
