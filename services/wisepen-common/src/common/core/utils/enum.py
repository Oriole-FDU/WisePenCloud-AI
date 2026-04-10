def auto_enum(*fields: str):
    """
    自动为枚举类添加字段属性和查找方法。

    该装饰器会遍历枚举类的所有成员，将成员的值按顺序绑定到指定的字段名上，
    并为每个字段自动生成对应的 `get_by_<field>` 类方法，用于按字段值查找枚举成员。

    Args:
        *fields: 字段名列表，例如 "code", "desc"。

    Returns:
        装饰器函数，返回修改后的枚举类。

    Example:
        @auto_enum("code", "desc")
        class IdentityType(Enum):
            STUDENT = (1, "STUDENT")
            TEACHER = (2, "TEACHER")
            ADMIN = (3, "ADMIN")

        # 使用自动生成的属性
        print(IdentityType.STUDENT.code)   # 1
        print(IdentityType.STUDENT.desc)   # "STUDENT"

        # 使用自动生成的查找方法
        print(IdentityType.get_by_code(2))   # IdentityType.TEACHER
        print(IdentityType.get_by_desc("ADMIN"))  # IdentityType.ADMIN

    Note:
        - 如果枚举成员的值不是 tuple，会自动包装为单元素元组。
        - 字段数量必须与每个成员值的元素数量一致，否则抛出 ValueError。
        - 生成的 `get_by_<field>` 方法在找不到时返回 None，不会抛出异常。
    """
    def decorator(cls):
        for member in cls:
            value = member.value
            args = value if isinstance(value, tuple) else (value,)
            if len(args) != len(fields):
                raise ValueError(f"Expected {len(fields)} fields, got {len(args)}")
            for field, arg in zip(fields, args):
                setattr(member, field, arg)

        for field in fields:
            @classmethod
            def finder(cls, value, f=field):
                for member in cls:
                    if getattr(member, f) == value:
                        return member
                return None
            setattr(cls, f"get_by_{field}", finder)

        return cls
    return decorator