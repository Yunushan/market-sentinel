from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "market-sentinel"
DISPLAY_NAME = "MarketSentinel"
MANUFACTURER = "MarketSentinel contributors"
UPGRADE_CODE = str(uuid.uuid5(uuid.NAMESPACE_DNS, APP_NAME)).upper()
COMPONENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/Yunushan/{APP_NAME}/windows-msi")


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def xml_attr(value: object) -> str:
    return escape(str(value), {'"': "&quot;"})


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def prepare_frontend_dist(frontend_zip: Path | None) -> Path:
    frontend_dist = ROOT / "frontend" / "dist"
    if frontend_zip is not None:
        clean_dir(frontend_dist)
        with ZipFile(frontend_zip) as archive:
            archive.extractall(frontend_dist)
    if not (frontend_dist / "index.html").exists():
        raise SystemExit(
            "frontend/dist/index.html is missing. Build the React frontend first or pass --frontend-zip."
        )
    return frontend_dist


def build_pyinstaller(work_dir: Path, package_dir: Path) -> None:
    pyinstaller_dist = work_dir / "pyinstaller-dist"
    pyinstaller_build = work_dir / "pyinstaller-build"
    clean_dir(pyinstaller_dist)
    clean_dir(pyinstaller_build)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        APP_NAME,
        "--icon",
        str(ROOT / "assets" / "marketsentinel.ico"),
        "--distpath",
        str(pyinstaller_dist),
        "--workpath",
        str(pyinstaller_build),
        "--specpath",
        str(work_dir),
        "--hidden-import",
        "web_api",
        "--collect-submodules",
        "core",
        "--collect-submodules",
        "market_adapters",
        "--collect-submodules",
        "polymarket",
        str(ROOT / "app.py"),
    ]
    run(command)

    built_app = pyinstaller_dist / APP_NAME
    if not (built_app / f"{APP_NAME}.exe").exists():
        raise SystemExit(f"PyInstaller did not produce {APP_NAME}.exe.")
    if package_dir.exists():
        shutil.rmtree(package_dir)
    shutil.copytree(built_app, package_dir)


def windows_config_bootstrap() -> str:
    return textwrap.dedent(
        f"""\
        set "APP_DATA_DIR=%~dp0data"
        set "LOCAL_CONFIG=%APP_DATA_DIR%\\config.json"
        set "APP_CONFIG_DIR=%APPDATA%\\{APP_NAME}\\data"
        set "APP_CONFIG_PATH=%LOCAL_CONFIG%"
        if not exist "%APP_DATA_DIR%" mkdir "%APP_DATA_DIR%"
        2>nul (>>"%APP_DATA_DIR%\\.write-test" echo.) && (
            del "%APP_DATA_DIR%\\.write-test" >nul 2>nul
        ) || (
            if not exist "%APP_CONFIG_DIR%" mkdir "%APP_CONFIG_DIR%"
            set "APP_CONFIG_PATH=%APP_CONFIG_DIR%\\config.json"
            if not exist "%APP_CONFIG_PATH%" if exist "%~dp0data\\config.example.json" copy "%~dp0data\\config.example.json" "%APP_CONFIG_PATH%" >nul
        )
        set "PREDICTION_MARKET_CONFIG_PATH=%APP_CONFIG_PATH%"
        """
    )


def write_launcher_files(package_dir: Path, version: str) -> None:
    (package_dir / "start_tkinter_gui.bat").write_text(
        textwrap.dedent(
            f"""\
            @echo off
            setlocal
            cd /d "%~dp0"
            {windows_config_bootstrap()}
            "%~dp0{APP_NAME}.exe"
            """
        ),
        encoding="utf-8",
    )
    (package_dir / "start_web_gui.bat").write_text(
        textwrap.dedent(
            f"""\
            @echo off
            setlocal
            cd /d "%~dp0"
            {windows_config_bootstrap()}
            if "%API_PORT%"=="" set "API_PORT=8765"
            echo React production GUI: http://127.0.0.1:%API_PORT%
            echo Tkinter fallback: start_tkinter_gui.bat
            echo Config path: %PREDICTION_MARKET_CONFIG_PATH%
            start "" "http://127.0.0.1:%API_PORT%"
            "%~dp0{APP_NAME}.exe" --web-gui --host 127.0.0.1 --port %API_PORT% --config "%PREDICTION_MARKET_CONFIG_PATH%" --frontend-dir "%~dp0frontend\\dist"
            """
        ),
        encoding="utf-8",
    )
    (package_dir / "README_WINDOWS.txt").write_text(
        textwrap.dedent(
            f"""\
            {DISPLAY_NAME} {version}

            Start options:
            - start_tkinter_gui.bat launches the desktop Tkinter GUI.
            - start_web_gui.bat serves the bundled React GUI at http://127.0.0.1:8765.
            - {APP_NAME}.exe launches Tkinter directly.
            - {APP_NAME}.exe --web-gui --frontend-dir frontend\\dist serves the React GUI.

            Local configuration:
            - data\\config.example.json is a full example configuration.
            - The launchers use data\\config.json when the package folder is writable.
            - If the package is installed under Program Files, the launchers use %APPDATA%\\{APP_NAME}\\data\\config.json.
            - .env.example documents optional credentials.

            Live trading remains guarded by configuration, credentials, geoblock checks, and adapter safety gates.
            """
        ),
        encoding="utf-8",
    )
    (package_dir / "VERSION.txt").write_text(f"{APP_NAME} {version}\n", encoding="utf-8")


def copy_release_payload(package_dir: Path, frontend_dist: Path, version: str) -> None:
    copy_file(ROOT / "README.md", package_dir / "README.md")
    copy_file(ROOT / "LICENSE", package_dir / "LICENSE")
    copy_file(ROOT / ".env.example", package_dir / ".env.example")
    copy_file(ROOT / "data" / "config.example.json", package_dir / "data" / "config.example.json")
    copy_file(ROOT / "assets" / "marketsentinel.ico", package_dir / "assets" / "marketsentinel.ico")
    copy_file(ROOT / "marketsentinel.png", package_dir / "assets" / "marketsentinel.png")
    icon_dir = ROOT / "assets" / "icons"
    if icon_dir.exists():
        icon_target = package_dir / "assets" / "icons"
        if icon_target.exists():
            shutil.rmtree(icon_target)
        shutil.copytree(icon_dir, icon_target)

    frontend_target = package_dir / "frontend" / "dist"
    if frontend_target.exists():
        shutil.rmtree(frontend_target)
    shutil.copytree(frontend_dist, frontend_target)
    write_launcher_files(package_dir, version)


def make_portable_zip(package_dir: Path, output_dir: Path, tag: str) -> Path:
    zip_path = output_dir / f"{APP_NAME}-{tag}-win-x64.zip"
    root_name = f"{APP_NAME}-{tag}-win-x64"
    if zip_path.exists():
        zip_path.unlink()
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path(root_name) / path.relative_to(package_dir))
    return zip_path


def msi_product_version(version: str) -> str:
    base = version.split("-", 1)[0]
    parts = base.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit(f"MSI version must come from a numeric x.y.z tag; got {version!r}.")
    return ".".join(str(int(part)) for part in parts)


def directory_id(relative_dir: Path) -> str:
    if str(relative_dir) in ("", "."):
        return "INSTALLFOLDER"
    digest = uuid.uuid5(COMPONENT_NAMESPACE, relative_dir.as_posix()).hex[:16]
    return f"dir_{digest}"


def build_directory_tree(package_dir: Path) -> dict[Path, list[Path]]:
    tree: dict[Path, list[Path]] = {}
    for directory in sorted(path for path in package_dir.rglob("*") if path.is_dir()):
        relative = directory.relative_to(package_dir)
        parent = relative.parent
        tree.setdefault(parent, [])
        tree.setdefault(relative, [])
        if relative not in tree[parent]:
            tree[parent].append(relative)
    tree.setdefault(Path("."), [])
    return tree


def render_directory_xml(tree: dict[Path, list[Path]], relative: Path = Path("."), indent: int = 8) -> list[str]:
    lines: list[str] = []
    pad = " " * indent
    for child in sorted(tree.get(relative, []), key=lambda value: value.as_posix()):
        lines.append(f'{pad}<Directory Id="{directory_id(child)}" Name="{xml_attr(child.name)}">')
        lines.extend(render_directory_xml(tree, child, indent + 2))
        lines.append(f"{pad}</Directory>")
    return lines


def generate_wxs(package_dir: Path, wxs_path: Path, version: str) -> None:
    tree = build_directory_tree(package_dir)
    directory_lines = render_directory_xml(tree)
    component_lines: list[str] = []

    for path in sorted(file for file in package_dir.rglob("*") if file.is_file()):
        relative = path.relative_to(package_dir)
        digest = uuid.uuid5(COMPONENT_NAMESPACE, relative.as_posix()).hex[:16]
        component_guid = str(uuid.uuid5(COMPONENT_NAMESPACE, f"component:{relative.as_posix()}")).upper()
        component_lines.extend(
            [
                f'      <Component Id="cmp_{digest}" Directory="{directory_id(relative.parent)}" Guid="{component_guid}">',
                f'        <File Id="fil_{digest}" Source="{xml_attr(path)}" KeyPath="yes" />',
                "      </Component>",
            ]
        )

    shortcut_guid = str(uuid.uuid5(COMPONENT_NAMESPACE, "component:start-menu-shortcuts")).upper()
    component_lines.extend(
        [
            f'      <Component Id="cmp_start_menu_shortcuts" Directory="ApplicationProgramsFolder" Guid="{shortcut_guid}">',
            (
                f'        <Shortcut Id="StartMenuTkinterShortcut" Name="{xml_attr(DISPLAY_NAME)}" '
                'Target="[INSTALLFOLDER]start_tkinter_gui.bat" WorkingDirectory="INSTALLFOLDER" />'
            ),
            (
                f'        <Shortcut Id="StartMenuWebShortcut" Name="{xml_attr(DISPLAY_NAME)} Web GUI" '
                'Target="[INSTALLFOLDER]start_web_gui.bat" WorkingDirectory="INSTALLFOLDER" />'
            ),
            '        <RemoveFolder Id="ApplicationProgramsFolder" On="uninstall" />',
            (
                f'        <RegistryValue Root="HKLM" Key="Software\\{xml_attr(APP_NAME)}" '
                'Name="InstallDir" Type="string" Value="[INSTALLFOLDER]" KeyPath="yes" />'
            ),
            "      </Component>",
        ]
    )

    wxs_path.write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">',
                (
                    f'  <Package Name="{xml_attr(DISPLAY_NAME)}" Manufacturer="{xml_attr(MANUFACTURER)}" '
                    f'Version="{msi_product_version(version)}" UpgradeCode="{UPGRADE_CODE}" Scope="perMachine">'
                ),
                '    <MajorUpgrade DowngradeErrorMessage="A newer version of this application is already installed." />',
                '    <MediaTemplate EmbedCab="yes" />',
                '    <StandardDirectory Id="ProgramFilesFolder">',
                f'      <Directory Id="INSTALLFOLDER" Name="{xml_attr(DISPLAY_NAME)}">',
                *directory_lines,
                "      </Directory>",
                "    </StandardDirectory>",
                '    <StandardDirectory Id="ProgramMenuFolder">',
                f'      <Directory Id="ApplicationProgramsFolder" Name="{xml_attr(DISPLAY_NAME)}" />',
                "    </StandardDirectory>",
                f'    <Feature Id="MainFeature" Title="{xml_attr(DISPLAY_NAME)}" Level="1">',
                '      <ComponentGroupRef Id="ApplicationFiles" />',
                "    </Feature>",
                "  </Package>",
                "  <Fragment>",
                '    <ComponentGroup Id="ApplicationFiles">',
                *component_lines,
                "    </ComponentGroup>",
                "  </Fragment>",
                "</Wix>",
                "",
            ]
        ),
        encoding="utf-8",
    )


def build_msi(package_dir: Path, output_dir: Path, work_dir: Path, tag: str, version: str) -> Path:
    wix = shutil.which("wix")
    if not wix:
        raise SystemExit("WiX Toolset CLI `wix` was not found on PATH. Install it before building the MSI.")

    wxs_path = work_dir / "installer.wxs"
    msi_path = output_dir / f"{APP_NAME}-{tag}-win-x64.msi"
    generate_wxs(package_dir, wxs_path, version)
    if msi_path.exists():
        msi_path.unlink()
    run([wix, "build", str(wxs_path), "-out", str(msi_path)])
    return msi_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Windows portable zip and MSI release artifacts.")
    parser.add_argument("--version", required=True, help="Release version without leading v, for example 1.0.0.")
    parser.add_argument("--tag", required=True, help="Release tag, for example v1.0.0.")
    parser.add_argument("--frontend-zip", type=Path, help="Zip produced by the frontend-dist release job.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release-assets")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build" / "windows-release")
    parser.add_argument("--skip-msi", action="store_true", help="Build only the portable zip.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.name != "nt":
        raise SystemExit("Windows release artifacts must be built on Windows.")

    output_dir = args.output_dir.resolve()
    work_dir = args.work_dir.resolve()
    package_dir = work_dir / f"{APP_NAME}-{args.tag}-win-x64"
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_dir(work_dir)

    frontend_dist = prepare_frontend_dist(args.frontend_zip.resolve() if args.frontend_zip else None)
    build_pyinstaller(work_dir, package_dir)
    copy_release_payload(package_dir, frontend_dist, args.version)
    portable_zip = make_portable_zip(package_dir, output_dir, args.tag)
    print(f"Built portable zip: {portable_zip}")
    if not args.skip_msi:
        msi_path = build_msi(package_dir, output_dir, work_dir, args.tag, args.version)
        print(f"Built MSI: {msi_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
