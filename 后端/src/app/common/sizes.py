"""Human-readable byte sizes for user-facing limits and error messages."""


def human_size(value: int) -> str:
    """整数字节转成提示文案用的单位；只用于展示，不参与任何校验计算。"""
    if value >= 1024 * 1024:
        megabytes = value / (1024 * 1024)
        return f"{megabytes:.0f} MB" if megabytes >= 10 else f"{megabytes:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value} 字节"
