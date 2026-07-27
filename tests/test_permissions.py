"""permissions 单测:三态(ask/allow/deny)+ 工具 glob 匹配(COMPONENTS §7)。"""

from openbimagent.core.permissions import DEFAULT_RULES, Permission, check_permission


def test_default_rules() -> None:
    """全局默认:读 allow、MCP 写 ask、execute_*_code ask;未列名默认 ask。"""
    assert check_permission("read", {}) is Permission.ALLOW
    assert check_permission("mcp_call:blender.set_material", {}) is Permission.ASK  # mcp_* glob
    assert check_permission("execute_blender_code", {}) is Permission.ASK
    assert check_permission("write", {}) is Permission.ASK  # 兜底默认 ask


def test_three_states_via_rules() -> None:
    """角色 frontmatter 覆盖:allow / deny / ask 三态各自生效。"""
    rules = {"write": Permission.ALLOW, "deliver": Permission.DENY, "bash": Permission.ASK}
    assert check_permission("write", rules) is Permission.ALLOW
    assert check_permission("deliver", rules) is Permission.DENY
    assert check_permission("bash", rules) is Permission.ASK


def test_glob_full_name_and_base_name() -> None:
    """glob 匹配全名与基名:bash:rm * 精确拦截危险命令,其余 bash 按基名规则放行。"""
    rules = {"bash": Permission.ALLOW, "bash:rm *": Permission.DENY}
    assert check_permission("bash:rm -rf /", rules) is Permission.DENY  # 命中 bash:rm *
    assert check_permission("bash:ls -la", rules) is Permission.ALLOW  # 基名 bash 精确命中


def test_glob_longest_pattern_wins() -> None:
    """多条 glob 命中时取最长(最具体)pattern;mcp_call* 覆盖默认 mcp_*。"""
    assert check_permission("mcp_call:server.tool", {}) is Permission.ASK  # DEFAULT_RULES mcp_*
    rules = {"mcp_call*": Permission.DENY}
    assert check_permission("mcp_call:server.tool", rules) is Permission.DENY
    assert check_permission("mcp_other", rules) is Permission.ASK  # 仍落 mcp_*


def test_rules_do_not_mutate_defaults() -> None:
    """合并规则不回写 DEFAULT_RULES。"""
    check_permission("bash", {"bash": Permission.ALLOW})
    assert "bash" not in DEFAULT_RULES
