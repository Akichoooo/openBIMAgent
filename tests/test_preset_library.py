"""预置库集成测试(Relay 018 任务 E2)。

覆盖 4 项(不依赖 .blend 实际生成,只校验目录/文档/脚本语法):
- 预置库目录存在(materials/ + geonodes/)
- README.md 文档完整(materials 含 5 材质 + Infinigen;geonodes 含 3 修改器 + GeoNodes)
- generate_materials.py 存在且语法正确(compile)
- generate_geonodes.py 存在且语法正确(compile)

不调 Blender:compile() 只解析语法不执行 import bpy,无需 Blender 安装。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "mcp_servers" / "blender_mcp" / "assets"
MATERIALS_DIR = ASSETS_DIR / "materials"
GEONODES_DIR = ASSETS_DIR / "geonodes"


def test_preset_library_directories_exist():
    """预置库目录 materials/ + geonodes/ 都存在。"""
    assert MATERIALS_DIR.is_dir(), f"材质库目录应存在:{MATERIALS_DIR}"
    assert GEONODES_DIR.is_dir(), f"GeoNodes 库目录应存在:{GEONODES_DIR}"


def test_readme_docs_complete():
    """README.md 文档完整:materials 含 5 材质 + Infinigen;geonodes 含 3 修改器 + GeoNodes。"""
    mat_readme = MATERIALS_DIR / "README.md"
    geo_readme = GEONODES_DIR / "README.md"
    assert mat_readme.is_file(), "materials/README.md 应存在"
    assert geo_readme.is_file(), "geonodes/README.md 应存在"

    mat_text = mat_readme.read_text(encoding="utf-8")
    assert "Infinigen" in mat_text, "materials/README 应含 Infinigen 风格说明"
    # 5 个材质节点组名都应在 README
    for name in ["MetalWorn", "WoodOak", "ConcreteRough", "PlasticMatte", "GlassFrosted"]:
        assert name in mat_text, f"materials/README 应含材质 {name}"

    geo_text = geo_readme.read_text(encoding="utf-8")
    assert "GeoNodes" in geo_text, "geonodes/README 应含 GeoNodes 说明"
    # 3 个修改器节点组名都应在 README
    for name in ["DamageEdgeWear", "DamageRustSpots", "DamageScratches"]:
        assert name in geo_text, f"geonodes/README 应含修改器 {name}"


def test_generate_materials_syntax():
    """generate_materials.py 存在且语法正确(compile,不执行 import bpy)。"""
    script = MATERIALS_DIR / "generate_materials.py"
    assert script.is_file(), "generate_materials.py 应存在"
    source = script.read_text(encoding="utf-8")
    # compile 只解析语法,不执行 import(无需 Blender 安装)
    compile(source, str(script), "exec")  # 抛 SyntaxError 即失败


def test_generate_geonodes_syntax():
    """generate_geonodes.py 存在且语法正确(compile,不执行 import bpy)。"""
    script = GEONODES_DIR / "generate_geonodes.py"
    assert script.is_file(), "generate_geonodes.py 应存在"
    source = script.read_text(encoding="utf-8")
    compile(source, str(script), "exec")  # 抛 SyntaxError 即失败
