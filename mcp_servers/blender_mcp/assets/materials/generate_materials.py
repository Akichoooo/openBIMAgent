"""生成预置 procedural 材质库（Infinigen 风格）。

5 个材质节点组：
- MetalWorn      磨损金属（Noise 调制 Roughness）
- WoodOak        橡木（Wave Texture → Bump 木纹）
- ConcreteRough  粗糙混凝土（Noise → Bump 颗粒）
- PlasticMatte   哑光塑料（纯 Principled BSDF）
- GlassFrosted   磨砂玻璃（Transmission + Noise 调制 Roughness）

全部基于 Principled BSDF，纯 procedural（零外部贴图），参数暴露为 node group interface。
Blender 5.2 兼容：interface 用 group.interface.new_socket()（非旧版 group.inputs.new()）。

用法：
    D:/devloop/blender/blender.exe --background --factory-startup --python generate_materials.py
产物：materials.blend（与脚本同目录）。
"""

from __future__ import annotations

import bpy


def _add_socket(group, name, in_out, socket_type, default_value=None):
    """5.x 兼容：用 group.interface.new_socket() 创建接口并设默认值。"""
    sock = group.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default_value is not None:
        try:
            sock.default_value = default_value
        except (AttributeError, TypeError):
            pass
    return sock


def _new_material_group(name):
    """新建 ShaderNodeTree 材质节点组，预置 Input/Output 节点。"""
    group = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    g_in = group.nodes.new('NodeGroupInput')
    g_in.location = (-400, 0)
    g_out = group.nodes.new('NodeGroupOutput')
    g_out.location = (400, 0)
    return group, g_in, g_out


def _bsdf(group, location=(0, 0)):
    """新建 Principled BSDF 节点。"""
    bsdf = group.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = location
    return bsdf


def create_metal_worn():
    """磨损金属：Noise 调制 Roughness（磨损处更粗糙），Metallic=1.0。"""
    group, g_in, g_out = _new_material_group("MetalWorn")
    _add_socket(group, "Color", 'INPUT', 'NodeSocketColor', (0.55, 0.55, 0.60, 1.0))
    _add_socket(group, "Roughness", 'INPUT', 'NodeSocketFloat', 0.65)
    _add_socket(group, "Metallic", 'INPUT', 'NodeSocketFloat', 1.0)
    _add_socket(group, "WearAmount", 'INPUT', 'NodeSocketFloat', 0.4)
    _add_socket(group, "Shader", 'OUTPUT', 'NodeSocketShader')

    bsdf = _bsdf(group)
    # Noise 调制 Roughness：磨损量控制噪声幅度
    noise = group.nodes.new('ShaderNodeTexNoise')
    noise.location = (-400, -200)
    noise.inputs["Scale"].default_value = 12.0
    noise.inputs["Detail"].default_value = 6.0
    # Roughness = base + WearAmount * (noise * 0.5)
    mul_wear = group.nodes.new('ShaderNodeMath')
    mul_wear.operation = 'MULTIPLY'
    mul_wear.location = (-200, -200)
    mul_noise = group.nodes.new('ShaderNodeMath')
    mul_noise.operation = 'MULTIPLY'
    mul_noise.location = (-50, -250)
    add_rough = group.nodes.new('ShaderNodeMath')
    add_rough.operation = 'ADD'
    add_rough.location = (-100, -100)

    group.links.new(g_in.outputs["Color"], bsdf.inputs["Base Color"])
    group.links.new(g_in.outputs["Metallic"], bsdf.inputs["Metallic"])
    group.links.new(g_in.outputs["WearAmount"], mul_wear.inputs[0])
    group.links.new(noise.outputs["Fac"], mul_wear.inputs[1])
    group.links.new(mul_wear.outputs[0], mul_noise.inputs[0])
    mul_noise.inputs[1].default_value = 0.5
    group.links.new(g_in.outputs["Roughness"], add_rough.inputs[0])
    group.links.new(mul_noise.outputs[0], add_rough.inputs[1])
    group.links.new(add_rough.outputs[0], bsdf.inputs["Roughness"])
    group.links.new(bsdf.outputs["BSDF"], g_out.inputs["Shader"])
    return group


def create_wood_oak():
    """橡木：Wave Texture 做木纹 → Bump 影响法线 + 粗糙度。"""
    group, g_in, g_out = _new_material_group("WoodOak")
    _add_socket(group, "Color", 'INPUT', 'NodeSocketColor', (0.45, 0.30, 0.18, 1.0))
    _add_socket(group, "Roughness", 'INPUT', 'NodeSocketFloat', 0.6)
    _add_socket(group, "GrainScale", 'INPUT', 'NodeSocketFloat', 8.0)
    _add_socket(group, "Shader", 'OUTPUT', 'NodeSocketShader')

    bsdf = _bsdf(group)
    wave = group.nodes.new('ShaderNodeTexWave')
    wave.location = (-400, -200)
    wave.wave_type = 'BANDS'
    wave.inputs["Scale"].default_value = 8.0
    bump = group.nodes.new('ShaderNodeBump')
    bump.location = (-200, -200)
    bump.inputs["Strength"].default_value = 0.3

    group.links.new(g_in.outputs["GrainScale"], wave.inputs["Scale"])
    group.links.new(wave.outputs["Fac"], bump.inputs["Height"])
    group.links.new(g_in.outputs["Color"], bsdf.inputs["Base Color"])
    group.links.new(g_in.outputs["Roughness"], bsdf.inputs["Roughness"])
    group.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    group.links.new(bsdf.outputs["BSDF"], g_out.inputs["Shader"])
    return group


def create_concrete_rough():
    """粗糙混凝土：Noise → Bump 颗粒感，高粗糙度。"""
    group, g_in, g_out = _new_material_group("ConcreteRough")
    _add_socket(group, "Color", 'INPUT', 'NodeSocketColor', (0.62, 0.60, 0.58, 1.0))
    _add_socket(group, "Roughness", 'INPUT', 'NodeSocketFloat', 0.85)
    _add_socket(group, "BumpScale", 'INPUT', 'NodeSocketFloat', 0.5)
    _add_socket(group, "Shader", 'OUTPUT', 'NodeSocketShader')

    bsdf = _bsdf(group)
    noise = group.nodes.new('ShaderNodeTexNoise')
    noise.location = (-400, -200)
    noise.inputs["Scale"].default_value = 20.0
    noise.inputs["Detail"].default_value = 8.0
    bump = group.nodes.new('ShaderNodeBump')
    bump.location = (-200, -200)

    group.links.new(g_in.outputs["BumpScale"], bump.inputs["Strength"])
    group.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    group.links.new(g_in.outputs["Color"], bsdf.inputs["Base Color"])
    group.links.new(g_in.outputs["Roughness"], bsdf.inputs["Roughness"])
    group.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    group.links.new(bsdf.outputs["BSDF"], g_out.inputs["Shader"])
    return group


def create_plastic_matte():
    """哑光塑料：纯 Principled BSDF，Metallic=0，中高粗糙度。"""
    group, g_in, g_out = _new_material_group("PlasticMatte")
    _add_socket(group, "Color", 'INPUT', 'NodeSocketColor', (0.20, 0.20, 0.22, 1.0))
    _add_socket(group, "Roughness", 'INPUT', 'NodeSocketFloat', 0.55)
    _add_socket(group, "Shader", 'OUTPUT', 'NodeSocketShader')

    bsdf = _bsdf(group)
    bsdf.inputs["Metallic"].default_value = 0.0
    group.links.new(g_in.outputs["Color"], bsdf.inputs["Base Color"])
    group.links.new(g_in.outputs["Roughness"], bsdf.inputs["Roughness"])
    group.links.new(bsdf.outputs["BSDF"], g_out.inputs["Shader"])
    return group


def create_glass_frosted():
    """磨砂玻璃：Transmission=1，Noise 调制 Roughness 制造磨砂感。"""
    group, g_in, g_out = _new_material_group("GlassFrosted")
    _add_socket(group, "Color", 'INPUT', 'NodeSocketColor', (0.85, 0.90, 0.95, 1.0))
    _add_socket(group, "Roughness", 'INPUT', 'NodeSocketFloat', 0.25)
    _add_socket(group, "IOR", 'INPUT', 'NodeSocketFloat', 1.45)
    _add_socket(group, "Shader", 'OUTPUT', 'NodeSocketShader')

    bsdf = _bsdf(group)
    # 5.x Transmission 输入名为 "Transmission Weight"（4.x 旧名 "Transmission"）
    tx_input = bsdf.inputs.get("Transmission Weight") or bsdf.inputs.get("Transmission")
    if tx_input is not None:
        tx_input.default_value = 1.0
    # Noise 调制 Roughness → 磨砂不均匀
    noise = group.nodes.new('ShaderNodeTexNoise')
    noise.location = (-400, -200)
    noise.inputs["Scale"].default_value = 50.0
    mul = group.nodes.new('ShaderNodeMath')
    mul.operation = 'MULTIPLY'
    mul.location = (-200, -200)
    mul.inputs[1].default_value = 0.15
    add = group.nodes.new('ShaderNodeMath')
    add.operation = 'ADD'
    add.location = (-50, -200)

    group.links.new(g_in.outputs["Color"], bsdf.inputs["Base Color"])
    group.links.new(g_in.outputs["IOR"], bsdf.inputs["IOR"])
    group.links.new(noise.outputs["Fac"], mul.inputs[0])
    group.links.new(g_in.outputs["Roughness"], add.inputs[0])
    group.links.new(mul.outputs[0], add.inputs[1])
    group.links.new(add.outputs[0], bsdf.inputs["Roughness"])
    group.links.new(bsdf.outputs["BSDF"], g_out.inputs["Shader"])
    return group


def save_materials():
    """创建全部 5 个材质节点组并保存到 materials.blend。"""
    create_metal_worn()
    create_wood_oak()
    create_concrete_rough()
    create_plastic_matte()
    create_glass_frosted()

    names = sorted(n.name for n in bpy.data.node_groups)
    print(f"[generate_materials] created node_groups: {names}")

    import os
    # 脚本可能从任意 cwd 调用，落点固定为脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(script_dir, "materials.blend")
    bpy.ops.wm.save_as_mainfile(filepath=out_path)
    print(f"[generate_materials] saved: {out_path}")


if __name__ == "__main__":
    save_materials()
