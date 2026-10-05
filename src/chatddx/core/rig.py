import importlib
import os
from importlib import metadata

from chatddx.factors.base import Code


# The code running now, as recorded in compilations and runs. The revision comes from
# CHATDDX_REVISION, set where the code is built or deployed; there is none otherwise.
def rig() -> Code:
    return Code(
        distribution="chatddx",
        version=metadata.version("chatddx"),
        revision=os.environ.get("CHATDDX_REVISION") or None,
    )


# What a tool's or a scorer's entry point names, in the code it pins. Only the running
# chatddx can be run: pinned code that isn't it is refused rather than run unpinned.
def entry(code: Code, entry_point: str) -> object:
    running = rig()
    if code != running:
        raise LookupError(
            f"{entry_point} pins {_shown(code)}, but {_shown(running)} is running"
        )
    module, _, attribute = entry_point.partition(":")
    try:
        found: object = importlib.import_module(module)
        for part in attribute.split("."):
            found = getattr(found, part)
    except (ImportError, AttributeError) as e:
        raise LookupError(
            f"{entry_point} names nothing in {_shown(code)}: {e}"
        ) from None
    return found


def _shown(code: Code) -> str:
    at = "" if code.revision is None else f" at {code.revision}"
    return f"{code.distribution} {code.version}{at}"
