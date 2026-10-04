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
