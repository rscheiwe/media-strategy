"""The fixed program each analysis subprocess runs (copied into its workspace as runner.py).

It is not imported by the API. It runs as `python -I runner.py` in a throwaway
workspace and executes the model's `analysis.py` with two names in scope:

    data   dict of dataset id -> pandas DataFrame, the rows `get_case_data` fetched
    pd     pandas

There is no database here. The API process fetches the rows (`get_case_data`) and
hands them over in `datasets.json`; the analysis itself (grouping, rates, budget
math) is the Python's job.

If the code ends on a bare expression (`df`, `results`), its value is printed, as
in a notebook: models often write code that way and would otherwise print nothing.

Errors are reported as one line in `.meta.json`, never as a traceback: the
traceback names internal paths and is noise to the model.
"""

import ast
import json
import re
import sys

FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.S)


def unfence(code):
    """Models sometimes wrap the code in a markdown fence; the fence isn't Python."""
    match = FENCE.match(code)
    return match.group(1) if match else code


def show(value):
    """Print a trailing expression's value the way a notebook would. A tuple, list or
    dict of results is shown one item at a time, so a DataFrame inside it isn't
    squeezed into a one-line repr."""
    if value is None:
        return
    if isinstance(value, dict) and any(hasattr(v, "to_string") for v in value.values()):
        for key, item in value.items():
            print(f"{key}:")
            show(item)
        return
    if isinstance(value, (tuple, list)) and any(hasattr(v, "to_string") for v in value):
        for item in value:
            show(item)
            print()
        return
    to_string = getattr(value, "to_string", None)
    print(to_string() if callable(to_string) else repr(value))


def number(value):
    """Readable numbers for the model: 1,666,667 not 1.666667e+06; 0.01148 not 0.011483."""
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.4g}"


def configure_pandas(pandas):
    """Pandas truncates wide frames to `...` by default. A model that can't see a column
    fills it in from imagination, so show every row and column (output is capped anyway)."""
    pandas.set_option("display.max_columns", None)
    pandas.set_option("display.max_rows", 200)
    pandas.set_option("display.width", 10_000)
    pandas.set_option("display.float_format", number)


def compile_like_notebook(code):
    """Compile `code`; if its last statement is a bare expression, display its value."""
    tree = ast.parse(code, "analysis.py")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        call = ast.Expr(ast.Call(ast.Name("__show__", ast.Load()), [last.value], []))
        tree.body[-1] = ast.copy_location(call, last)
        ast.fix_missing_locations(tree)
    return compile(tree, "analysis.py", "exec")


def one_line(exc, limit=400):
    """The exception as one line, collapsed rather than cut at the first newline, so a
    cause that comes after a multi-line preamble isn't lost."""
    if isinstance(exc, SyntaxError):
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"
    message = " ".join(str(exc).split())
    if len(message) > limit:
        message = message[: limit // 2] + " … " + message[-limit // 2 :]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    meta = {"error": None}
    try:
        import pandas

        configure_pandas(pandas)
        with open("datasets.json", encoding="utf-8") as source:
            raw = json.load(source)
        data = {key: pandas.DataFrame(rows) for key, rows in raw.items()}
        with open("analysis.py", encoding="utf-8") as source:
            code = unfence(source.read())
        scope = {"__name__": "__main__", "data": data, "pd": pandas, "__show__": show}
        exec(compile_like_notebook(code), scope)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            meta["error"] = f"SystemExit: {exc.code}"
    except BaseException as exc:  # noqa: BLE001 - every failure becomes one line
        meta["error"] = one_line(exc)
    finally:
        sys.stdout.flush()
        with open(".meta.json", "w", encoding="utf-8") as out:
            json.dump(meta, out)
    sys.exit(1 if meta["error"] else 0)


if __name__ == "__main__":
    main()
