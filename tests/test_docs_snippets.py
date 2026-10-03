"""Every Python snippet in the documentation uses the API that exists.

The examples in examples/ are run against the sandbox (test_sandbox.py and by
hand); the shorter snippets on the documentation pages and in the README are
parsed here, and every method, attribute and keyword argument they use on the
SDK's objects is looked up on the real classes. A snippet that calls something
the SDK does not have fails this test.
"""

import ast
import dataclasses
import inspect
import re
from pathlib import Path

import pytest

from zatca_tools import (
    Chain,
    ComplianceReport,
    Credentials,
    CsidResult,
    CsrRequest,
    Invoice,
    InvoiceDesign,
    KeyPair,
    Message,
    SubmissionResult,
    ValidationError,
    Zatca,
)
from zatca_tools.onboarding import Onboarding

SDK = Path(__file__).resolve().parents[1]
SITE_VIEWS = SDK.parents[1] / "resources" / "views" / "docs" / "sdk"

#: What a variable in the snippets stands for.
NAMES = {
    "zatca": Zatca, "invoice": Invoice, "receipt": Invoice, "b2b_invoice": Invoice, "note": Invoice,
    "result": SubmissionResult, "cleared": SubmissionResult, "credentials": Credentials, "error": ValidationError,
    "message": Message, "onboarding": Onboarding, "keys": KeyPair, "compliance": CsidResult, "production": CsidResult,
    "report": ComplianceReport, "problem": dict,
}
#: What an attribute of one of those returns.
RETURNS = {
    (Zatca, "chain"): Chain, (Zatca, "credentials"): Credentials, (SubmissionResult, "error"): Message,
    (Zatca, "onboarding"): Onboarding,
}
CALLS = {"Zatca": Zatca, "InvoiceDesign": InvoiceDesign, "CsrRequest": CsrRequest}


def members(cls):
    names = set(dir(cls))
    if dataclasses.is_dataclass(cls):
        names |= {f.name for f in dataclasses.fields(cls)}
    if cls is Zatca:
        names |= set(vars(Zatca("sandbox")))
    if cls is ValidationError:
        names |= set(vars(ValidationError("x")))
    return names


def snippets():
    found = []
    if SITE_VIEWS.is_dir():
        for view in sorted(SITE_VIEWS.glob("*.blade.php")):
            for i, code in enumerate(re.findall(r"<<<'PY'\n(.*?)\nPY;", view.read_text(encoding="utf-8"), re.S)):
                found.append((f"{view.name}#{i}", code))
    readme = (SDK / "README.md").read_text(encoding="utf-8")
    for i, code in enumerate(re.findall(r"```python\n(.*?)```", readme, re.S)):
        found.append((f"README.md#{i}", code))
    for example in sorted((SDK / "examples").glob("*.py")):
        found.append((example.name, example.read_text(encoding="utf-8")))
    return found


SNIPPETS = snippets()


def resolve(node):
    if isinstance(node, ast.Name):
        return NAMES.get(node.id)
    if isinstance(node, ast.Attribute):
        owner = resolve(node.value)
        return RETURNS.get((owner, node.attr)) if owner else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        owner = resolve(node.func.value)
        return RETURNS.get((owner, node.func.attr)) if owner else None
    return None


def test_there_are_snippets_to_check():
    assert len(SNIPPETS) >= 8


@pytest.mark.parametrize("where, code", SNIPPETS, ids=[w for w, _ in SNIPPETS])
def test_the_snippet_uses_the_real_api(where, code):
    tree = ast.parse(code, filename=where)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            owner = resolve(node.value)
            if owner is not None and owner is not dict:
                assert node.attr in members(owner), f"{where}: {owner.__name__} has no {node.attr}"
        if isinstance(node, ast.Call):
            target = None
            if isinstance(node.func, ast.Name) and node.func.id in CALLS:
                target = CALLS[node.func.id]
            elif isinstance(node.func, ast.Attribute):
                owner = resolve(node.func.value)
                if owner is not None and owner is not dict:
                    target = getattr(owner, node.func.attr, None)
            if target is not None and callable(target):
                parameters = inspect.signature(target).parameters
                if not any(p.kind is p.VAR_KEYWORD for p in parameters.values()):
                    for keyword in node.keywords:
                        if keyword.arg is not None:
                            assert keyword.arg in parameters, f"{where}: {getattr(target, '__qualname__', target)} takes no {keyword.arg}="
