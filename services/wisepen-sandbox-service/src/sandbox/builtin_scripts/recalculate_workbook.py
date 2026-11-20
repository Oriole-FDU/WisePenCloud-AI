"""Calculate a copy of an XLS or XLSX workbook with LibreOffice Calc."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

CALC_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Calc/Formula/Load">
    <prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop>
  </item>
</oor:items>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    workspace = Path.cwd().resolve()
    source = args.source.resolve()
    source.relative_to(workspace)
    output = args.output.resolve()
    path = output.relative_to(workspace)
    if source.suffix.lower() not in {".xls", ".xlsx"} or output.suffix.lower() != ".xlsx":
        parser.error("use an XLS or XLSX source and a new XLSX output")
    with TemporaryDirectory() as directory:
        stage = Path(directory)
        profile = stage / "profile"
        (profile / "user").mkdir(parents=True)
        # 入站 Excel 可以包含过期的保存值。强制 Calc 在加载 OOXML 时重算。
        (profile / "user" / "registrymodifications.xcu").write_text(CALC_PROFILE, encoding="utf-8")
        command = ["soffice", f"-env:UserInstallation={profile.as_uri()}", "--headless",
                   "--convert-to", "xlsx:Calc MS Excel 2007 XML"]
        if source.suffix.lower() == ".xls":
            # 将旧 BIFF 工作簿先转为 OOXML，统一走强制重算的加载路径。
            subprocess.run([*command, "--outdir", str(stage), str(source)],
                           check=True, timeout=120, stdout=subprocess.DEVNULL)
            source = stage / f"{source.stem}.xlsx"
        calculated = stage / "calculated"
        calculated.mkdir()
        subprocess.run([*command, "--outdir", str(calculated), str(source)],
                       check=True, timeout=120, stdout=subprocess.DEVNULL)
        with (calculated / f"{source.stem}.xlsx").open("rb") as converted:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as target:
                shutil.copyfileobj(converted, target)
    print(json.dumps({"path": path.as_posix(), "engine": "LibreOffice Calc"}))


if __name__ == "__main__":
    main()
