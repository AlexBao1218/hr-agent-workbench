"""Owner-only structured diagnostics; never provider HTTP bodies or reasoning."""
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4


def write_diagnostic(root,artifact):
    directory=Path(root)/'diagnostics'
    directory.mkdir(mode=0o700,exist_ok=True)
    directory.chmod(0o700)
    destination=directory/(artifact['runId']+'-'+uuid4().hex+'.json')
    fd,temporary=tempfile.mkstemp(prefix='.writing-',dir=directory)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as file:
            json.dump(artifact,file,ensure_ascii=False)
            file.flush();os.fsync(file.fileno())
        os.replace(temporary,destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return str(destination.relative_to(root))
